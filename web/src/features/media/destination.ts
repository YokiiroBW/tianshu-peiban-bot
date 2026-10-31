export function normalizeSubdirectory(value: string) {
  return value.trim().replaceAll("\\", "/");
}

// Catch obvious input mistakes; the server owns filename and filesystem validation.
export function subdirectoryInputError(value: string) {
  const path = normalizeSubdirectory(value);
  if (!path) return "";
  if (path.startsWith("/") || /^[A-Za-z]:/.test(path))
    return "请填写相对于所选媒体库根目录的子目录，例如 收藏夹/学习。";
  if (path.split("/").some((part) => !part || part === "." || part === ".."))
    return "子目录不能包含空层级、. 或 ..，例如 收藏夹/学习。";
  return "";
}

export function destinationPath(
  root: string | null | undefined,
  subdirectory = "",
) {
  if (!root) return null;
  const relative = normalizeSubdirectory(subdirectory);
  if (!relative) return root;
  const separator = root.includes("\\") && !root.includes("/") ? "\\" : "/";
  return `${root.replace(/[\\/]+$/, "")}${separator}${relative.replaceAll("/", separator)}`;
}

export function destinationLabel(
  root: string | null | undefined,
  subdirectory = "",
) {
  return (
    destinationPath(root, subdirectory) ??
    (subdirectory
      ? `子目录 ${subdirectory}（完整路径未提供）`
      : "目标根目录（完整路径未提供）")
  );
}
