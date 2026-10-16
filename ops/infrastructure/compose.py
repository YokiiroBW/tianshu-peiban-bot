"""Restricted Compose file projection and evidence-based desired/runtime drift.

Reuses the legacy dockgecollector's standard filenames, bounded node/alias walk,
merge precedence, unresolved interpolation and partial-observation rules. No
Compose command, environment expansion or referenced file loader is involved.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
import shlex

from .docker import DockerCollector
from .paging import page
from .protocol import Failure, MAX_INVENTORY
from .sampling import stable_id
from .transport import parse_sections, section_script

FILENAMES = ("compose.yaml", "docker-compose.yaml", "docker-compose.yml", "compose.yml")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
INTERPOLATION = re.compile(r"\$(?:\{|[A-Za-z_])")
MAX_FILE = 262144


class _YAML:
    def __init__(self, raw):
        try:
            import yaml
        except ImportError:
            raise Failure("compose_parser_unavailable", "unsupported") from None
        self.yaml = yaml
        self.steps = 0
        depth = aliases = events = 0
        try:
            for event in yaml.parse(raw, Loader=yaml.SafeLoader):
                events += 1
                if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
                    depth += 1
                elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
                    depth -= 1
                elif isinstance(event, yaml.AliasEvent):
                    aliases += 1
                if depth > 32 or aliases > 64 or events > 20000:
                    raise ValueError("YAML structure limit")
            documents = list(yaml.compose_all(raw, Loader=yaml.SafeLoader))
            if len(documents) != 1 or documents[0] is None:
                raise ValueError("exactly one YAML document required")
            self.root = documents[0]
            self._validate(self.root, set())
        except (yaml.YAMLError, ValueError, RecursionError):
            raise ValueError("compose_invalid") from None

    def _step(self):
        self.steps += 1
        if self.steps > 40000:
            raise ValueError("YAML expansion limit")

    def _validate(self, node, visiting):
        self._step()
        if id(node) in visiting:
            raise ValueError("recursive YAML alias")
        if node.tag not in {"tag:yaml.org,2002:" + value for value in ("str", "int", "bool", "null", "float", "merge", "map", "seq")}:
            raise ValueError("unsupported YAML tag")
        if isinstance(node, self.yaml.MappingNode):
            visiting.add(id(node))
            explicit = set()
            for key, value in node.value:
                if not isinstance(key, self.yaml.ScalarNode) or key.tag not in ("tag:yaml.org,2002:str", "tag:yaml.org,2002:merge") or key.value in explicit:
                    raise ValueError("duplicate or non-string YAML key")
                explicit.add(key.value)
                self._validate(value, visiting)
            visiting.remove(id(node))
        elif isinstance(node, self.yaml.SequenceNode):
            visiting.add(id(node))
            for value in node.value:
                self._validate(value, visiting)
            visiting.remove(id(node))

    def mapping(self, node, visiting=None):
        self._step()
        if not isinstance(node, self.yaml.MappingNode):
            raise ValueError("mapping required")
        visiting = set() if visiting is None else visiting
        if id(node) in visiting:
            raise ValueError("recursive YAML merge")
        visiting.add(id(node))
        result = {}
        for key, value in node.value:
            if key.value != "<<":
                continue
            mappings = value.value if isinstance(value, self.yaml.SequenceNode) else [value]
            for source in mappings:
                for name, source_value in self.mapping(source, visiting).items():
                    result.setdefault(name, source_value)
        for key, value in node.value:
            if key.value != "<<":
                result[key.value] = value
        visiting.remove(id(node))
        return result

    def scalar(self, node):
        self._step()
        return node.value if isinstance(node, self.yaml.ScalarNode) and node.tag == "tag:yaml.org,2002:str" else None

    def integer(self, node):
        if isinstance(node, self.yaml.ScalarNode) and node.tag == "tag:yaml.org,2002:int":
            try:
                if not re.fullmatch(r"0|[1-9][0-9]*", node.value):
                    return None
                value = int(node.value)
                return value if 0 <= value <= 2**64 - 1 else None
            except ValueError:
                return None
        return None

    def boolean(self, node, default=False):
        if node is None:
            return default
        if isinstance(node, self.yaml.ScalarNode) and node.tag == "tag:yaml.org,2002:bool":
            return node.value.lower() in ("true", "yes", "on")
        return None

    def sequence(self, node):
        if node is None:
            return []
        if not isinstance(node, self.yaml.SequenceNode):
            raise ValueError("sequence required")
        return node.value


def _image(value):
    if value is None:
        return {"state": "unspecified", "reference": None}
    if not value or len(value) > 512 or INTERPOLATION.search(value) or re.search(r"\s|://", value):
        return {"state": "unresolved", "reference": None}
    return {"state": "resolved", "reference": value}


def _port(parser, node):
    value = parser.scalar(node)
    item = {"target": None, "published": None, "protocol": "tcp", "host_ip": None, "state": "unresolved"}
    if value is None and isinstance(node, parser.yaml.ScalarNode) and node.tag == "tag:yaml.org,2002:int":
        value = node.value
    if value is not None:
        if INTERPOLATION.search(value):
            return item
        address = None
        if value.startswith("["):
            closing = value.find("]:")
            if closing < 0:
                return item
            address, value = value[1:closing], value[closing + 2:]
        if "/" in value:
            value, item["protocol"] = value.rsplit("/", 1)
        parts = value.split(":")
        if len(parts) == 3 and address is None:
            address, published, target = parts
        elif len(parts) == 2:
            published, target = parts
        elif len(parts) == 1:
            published, target = None, parts[0]
        else:
            return item
        item.update(target=int(target) if target.isdigit() else None, published=published, host_ip=address)
    else:
        values = parser.mapping(node)
        target = parser.integer(values.get("target"))
        published_node = values.get("published")
        published = parser.scalar(published_node)
        if published is None and published_node is not None and isinstance(published_node, parser.yaml.ScalarNode) and published_node.tag == "tag:yaml.org,2002:int":
            published = published_node.value
        item.update(target=target, published=published, protocol=parser.scalar(values.get("protocol")) or "tcp", host_ip=parser.scalar(values.get("host_ip")))
        if published_node is not None and published is None:
            return item
    valid = item["target"] is not None and 1 <= item["target"] <= 65535 and item["protocol"] in ("tcp", "udp", "sctp")
    valid = valid and (item["published"] is None or item["published"].isdigit() and 1 <= int(item["published"]) <= 65535)
    valid = valid and (item["host_ip"] is None or not INTERPOLATION.search(item["host_ip"]))
    if item["published"] is not None and len(item["published"]) > 512:
        item["published"], valid = None, False
    if item["host_ip"] is not None and len(item["host_ip"]) > 512:
        item["host_ip"], valid = None, False
    if item["protocol"] not in ("tcp", "udp", "sctp"):
        item["protocol"] = "unknown"
    if item["target"] is not None and not 1 <= item["target"] <= 65535:
        item["target"] = None
    item["state"] = "resolved" if valid else "unresolved"
    return item


def _mount(parser, node, directory, project, resources):
    item = {"kind": "unknown", "source": None, "target": None, "read_only": None, "state": "unresolved"}
    value = parser.scalar(node)
    if value is not None:
        if INTERPOLATION.search(value):
            return item
        parts = value.split(":")
        if len(parts) == 1:
            source, target, mode = None, parts[0], ""
        elif len(parts) in (2, 3):
            source, target, mode = parts[0], parts[1], parts[2] if len(parts) == 3 else ""
        else:
            return item
        kind = "bind" if source and (source.startswith(("/", ".", "~"))) else "volume"
        read_only = "ro" in mode.split(",")
        accepted_modes = {"ro", "rw", "z", "Z", "shared", "rshared", "slave", "rslave", "private", "rprivate", "nocopy", "cached", "delegated", "consistent"}
        if mode and (set(mode.split(",")) - accepted_modes or {"ro", "rw"} <= set(mode.split(","))):
            return item
    else:
        values = parser.mapping(node)
        kind = parser.scalar(values.get("type")) or "volume"
        source = parser.scalar(values.get("source"))
        target = parser.scalar(values.get("target"))
        read_only = parser.boolean(values.get("read_only"))
        if any(INTERPOLATION.search(value) for value in (source, target, kind) if value):
            return item
    item.update(kind=kind, source=source, target=target, read_only=read_only)
    if len(kind) > 512:
        item["kind"] = "unknown"
        return item
    if source is not None and len(source) > 4096:
        item["source"] = None
        return item
    if target is not None and len(target) > 4096:
        item["target"] = None
        return item
    if not target or not target.startswith("/") or posixpath.normpath(target) != target or len(target) > 4096 or read_only is None:
        return item
    if kind == "bind":
        if not source or source.startswith("~"):
            return item
        item["source"] = posixpath.normpath(posixpath.join(directory, source))
    elif kind == "volume":
        if source:
            if not NAME.fullmatch(source) or project is None:
                return item
            resource = resources.get(source)
            resolved = project + "_" + source
            if resource is not None:
                options = {} if isinstance(resource, parser.yaml.ScalarNode) and resource.tag == "tag:yaml.org,2002:null" else parser.mapping(resource)
                explicit_name = parser.scalar(options.get("name"))
                external = parser.boolean(options.get("external"))
                if external is None:
                    return item
                if explicit_name:
                    if INTERPOLATION.search(explicit_name):
                        return item
                    resolved = explicit_name
                elif external:
                    resolved = source
            item["source"] = resolved
    elif kind != "tmpfs":
        return item
    if len(item["source"] or "") > 4096:
        return item
    item["state"] = "resolved"
    return item


def unavailable_stack(node_id, name, reason):
    return {"id": stable_id("stack", node_id, name), "name": name, "project_name": None,
            "desired_revision": None, "state": "unavailable", "reason_codes": [reason],
            "services": [], "runtime_only_services": [], "drift_status": "unavailable"}


def parse_compose(raw, node_id, name, directory):
    if len(raw) > MAX_FILE:
        return unavailable_stack(node_id, name, "compose_file_too_large")
    stack = unavailable_stack(node_id, name, "compose_invalid")
    try:
        parser = _YAML(raw)
        top = parser.mapping(parser.root)
        project_node = top.get("name")
        project = parser.scalar(project_node) if project_node is not None else re.sub(r"[^a-z0-9_-]", "", name.lower()).lstrip("-_")
        reasons = []
        if not project or len(project) > 128 or INTERPOLATION.search(project) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project):
            project = None
            reasons.append("compose_project_unresolved")
        if top.get("include") is not None or top.get("extends") is not None:
            reasons.append("compose_unsupported")
        resources = parser.mapping(top["volumes"]) if top.get("volumes") is not None else {}
        services = parser.mapping(top.get("services"))
        if len(services) > 2000:
            reasons.append("compose_service_limit_reached")
        projected = []
        for service_name, service_node in sorted(services.items())[:2000]:
            if not NAME.fullmatch(service_name):
                reasons.append("compose_service_invalid")
                continue
            values = parser.mapping(service_node)
            service_reasons = []
            image = _image(parser.scalar(values.get("image")))
            if image["state"] != "resolved":
                service_reasons.append("compose_image_unresolved")
            if any(values.get(key) is not None for key in ("build", "extends", "profiles", "volumes_from", "configs", "secrets", "devices")):
                service_reasons.append("compose_unsupported")
            instances = parser.integer(values.get("scale")) if values.get("scale") is not None else 1
            if values.get("deploy") is not None:
                deploy = parser.mapping(values["deploy"])
                if deploy.get("replicas") is not None:
                    replicas = parser.integer(deploy["replicas"])
                    if values.get("scale") is not None and replicas != instances:
                        instances = None
                    else:
                        instances = replicas
                if deploy.get("mode") is not None and parser.scalar(deploy["mode"]) != "replicated":
                    instances = None
            if instances is None or instances > 10000:
                instances = None
                service_reasons.append("compose_scale_unresolved")
            ports = [_port(parser, port) for port in parser.sequence(values.get("ports"))[:1024]]
            mounts = [_mount(parser, mount, directory, project, resources) for mount in parser.sequence(values.get("volumes"))[:1024]]
            for tmpfs in parser.sequence(values.get("tmpfs"))[:1024]:
                target = parser.scalar(tmpfs)
                if target is not None and len(target) > 4096:
                    target = None
                mounts.append({"kind": "tmpfs", "source": None, "target": target,
                               "read_only": False, "state": "resolved" if target and target.startswith("/") and not INTERPOLATION.search(target) else "unresolved"})
            if len(parser.sequence(values.get("ports"))) > 1024 or len(parser.sequence(values.get("volumes"))) + len(parser.sequence(values.get("tmpfs"))) > 1024:
                service_reasons.append("compose_binding_limit_reached")
            mounts = mounts[:1024]
            if any(port["state"] == "unresolved" for port in ports) or any(mount["state"] == "unresolved" for mount in mounts):
                service_reasons.append("compose_binding_unresolved")
            projected.append({"name": service_name, "image": image, "desired_instances": instances,
                              "ports": ports, "mounts": mounts, "observed_container_ids": [],
                              "drift_status": "unavailable", "findings": [], "reason_codes": list(dict.fromkeys(service_reasons))})
            reasons.extend(service_reasons)
        stack.update(project_name=project, desired_revision=hashlib.sha256(raw).hexdigest(),
                     state="partial" if reasons else "ok", reason_codes=list(dict.fromkeys(reasons)), services=projected)
        return stack
    except Failure:
        raise
    except (ValueError, TypeError, RecursionError, AttributeError):
        return stack


def _normalized_image(value):
    repository = value.split("@", 1)[0]
    suffix = "@" + value.split("@", 1)[1] if "@" in value else ""
    repository = re.sub(r"^(?:docker.io|index.docker.io)/", "", repository)
    if repository.startswith("library/"):
        repository = repository[len("library/"):]
    if ":" not in repository.rsplit("/", 1)[-1] and not suffix:
        repository += ":latest"
    return repository + suffix


def compare_stack(stack, containers, *, runtime_complete=True, directory=None, filename=None):
    project = stack["project_name"]
    if project is None or stack["state"] == "unavailable":
        return stack
    actual = [item for item in containers if item["availability"] == "ok" and item["compose"] and item["compose"]["project"] == project and item["compose"]["oneoff"] is not True]
    actual_names = {item["compose"]["service"] for item in actual if item["compose"]["service"]}
    desired_names = {service["name"] for service in stack["services"]}
    stack["runtime_only_services"] = sorted(actual_names - desired_names)
    if not runtime_complete:
        stack["reason_codes"].append("runtime_inventory_partial")
        stack["state"] = "partial"
    for service in stack["services"]:
        instances = [item for item in actual if item["compose"]["service"] == service["name"]]
        service["observed_container_ids"] = [item["id"] for item in instances]
        findings = []
        uncertain = bool(service["reason_codes"] or stack["reason_codes"] or not runtime_complete)
        if service["desired_instances"] is not None and runtime_complete and len(instances) != service["desired_instances"]:
            findings.append("service_count_mismatch")
        for instance in instances:
            if instance["running"] is not True:
                findings.append("service_not_running")
            if instance["health"] in ("unhealthy", "starting"):
                findings.append("service_" + instance["health"])
            if instance["reason_codes"]:
                uncertain = True
            if service["image"]["state"] == "resolved":
                observed_image = instance["image_reference"]
                if observed_image is None:
                    uncertain = True
                elif _normalized_image(service["image"]["reference"]) != _normalized_image(observed_image):
                    findings.append("image_mismatch")
            if directory and instance["compose"]["working_dir"] != directory:
                uncertain = True
                service["reason_codes"].append("compose_working_dir_different")
            if filename and instance["compose"]["config_files"] != posixpath.join(directory, filename):
                uncertain = True
                service["reason_codes"].append("compose_runtime_files_different")
            for port in service["ports"]:
                if port["state"] != "resolved":
                    continue
                matches = [actual_port for actual_port in instance["ports"] if actual_port["container_port"] == port["target"] and actual_port["protocol"] == port["protocol"] and actual_port["host_port"] is not None]
                if not any((port["published"] is None or str(value["host_port"]) == port["published"]) and (port["host_ip"] is None or value["host_ip"] == port["host_ip"]) for value in matches):
                    findings.append("port_binding_mismatch")
            for mount in service["mounts"]:
                if mount["state"] != "resolved":
                    continue
                matches = [value for value in instance["mounts"] if value["target"] == mount["target"]]
                if not matches or not any(value["kind"] == mount["kind"] and value["read_only"] == mount["read_only"] and (mount["source"] is None or value["source"] == mount["source"]) for value in matches):
                    findings.append("mount_binding_mismatch")
        service["findings"] = list(dict.fromkeys(findings))
        service["reason_codes"] = list(dict.fromkeys(service["reason_codes"]))
        service["drift_status"] = "drifted" if findings else "unavailable" if uncertain else "in_sync"
    statuses = [item["drift_status"] for item in stack["services"]]
    stack["drift_status"] = "drifted" if stack["runtime_only_services"] or "drifted" in statuses else "unavailable" if stack["reason_codes"] or "unavailable" in statuses else "in_sync"
    if any(service["reason_codes"] for service in stack["services"]):
        stack["state"] = "partial"
        stack["reason_codes"].append("compose_comparison_partial")
    stack["reason_codes"] = list(dict.fromkeys(stack["reason_codes"]))
    return stack


def collect_compose(transport, node, params):
    if not node.stacks_root:
        raise Failure("stacks_not_configured", "unsupported")
    root = node.stacks_root
    quote = shlex.quote
    patterns = " -o ".join("-name " + quote(name) for name in FILENAMES)
    discovery = f'[ -d {quote(root)} ] && [ ! -L {quote(root)} ] && [ "$(cd -P {quote(root)} && pwd -P)" = {quote(root)} ] && find {quote(root)} -mindepth 2 -maxdepth 2 \\( -type f -o -type l \\) \\( {patterns} \\) -print'
    result = transport.script(discovery)
    if result.reason:
        raise Failure(result.reason)
    discovered = {}
    reasons = []
    for path in result.stdout.decode("utf-8", "replace").splitlines():
        relative = path.removeprefix(root.rstrip("/") + "/")
        parts = relative.split("/")
        if not path.startswith(root.rstrip("/") + "/") or len(parts) != 2 or not NAME.fullmatch(parts[0]) or parts[1] not in FILENAMES:
            reasons.append("stack_path_unsupported")
            continue
        discovered.setdefault(parts[0], set()).add(parts[1])
    names = sorted(discovered)
    if len(names) > MAX_INVENTORY:
        raise Failure("inventory_limit_exceeded")
    selected, cursor = page(names, node.id, "compose.inventory", params)
    commands = {}
    filenames = {}
    for index, name in enumerate(selected):
        filename = next(filename for filename in FILENAMES if filename in discovered[name])
        filenames[name] = filename
        directory = posixpath.join(root, name)
        path = posixpath.join(directory, filename)
        commands[f"stack_{index}"] = f'[ -d {quote(directory)} ] && [ ! -L {quote(directory)} ] && [ -f {quote(path)} ] && [ ! -L {quote(path)} ] && head -c {MAX_FILE + 1} {quote(path)}'
    read = transport.script(section_script(commands), max_output=3 * 1024 * 1024) if commands else None
    sections = parse_sections(read.stdout) if read else {}
    if read and read.reason:
        reasons.append(read.reason)
    containers = []
    runtime_complete = True
    try:
        docker = DockerCollector(transport, node)
        containers = docker.inspect(docker.identities())
        runtime_complete = all(item["availability"] == "ok" for item in containers)
    except Failure as failure:
        runtime_complete = False
        reasons.append(failure.code)
    stacks = []
    for index, name in enumerate(selected):
        code, raw = sections.get(f"stack_{index}", (1, b""))
        directory = posixpath.join(root, name)
        stack = parse_compose(raw, node.id, name, directory) if code == 0 else unavailable_stack(node.id, name, "compose_file_unavailable")
        if len(discovered[name]) > 1:
            stack["reason_codes"].append("compose_multiple_files")
            if stack["state"] == "ok":
                stack["state"] = "partial"
        stack = compare_stack(stack, containers, runtime_complete=runtime_complete, directory=directory, filename=filenames[name])
        reasons.extend(stack["reason_codes"])
        stacks.append(stack)
    return {"stacks": stacks}, list(dict.fromkeys(reasons)), len(names), cursor
