"""Reader script helpers backed by the existing bounded rule interpreter."""

import json
from urllib.parse import quote


def install_helpers(evaluator):
    from lxml import etree
    from LegadoParser2.RuleCompile import getRuleObj
    from LegadoParser2.RuleEval import getElements, getStrings

    def select(rule, strings=False):
        content = evaluator.get("result") or ""
        evaluate = getStrings if strings else getElements
        values = evaluate(content, getRuleObj(rule), evaluator)
        return json.dumps(
            [
                etree.tostring(value, encoding="unicode", method="html")
                if isinstance(value, etree._Element)
                else value
                for value in values
            ],
            ensure_ascii=False,
        )

    evaluator.context.add_callable("pyGetElements", lambda rule: select(rule))
    evaluator.context.add_callable("pyGetStringList", lambda rule: select(rule, True))
    evaluator.context.add_callable(
        "pyEncodeURI",
        lambda value, encoding: quote(str(value), safe="", encoding=encoding or "utf-8"),
    )
    evaluator.context.eval(
        """
        java.setContent = function(value) { globalThis.result = String(value); };
        java.getElements = function(rule) { return JSON.parse(pyGetElements(rule)); };
        java.getStringList = function(rule) { return JSON.parse(pyGetStringList(rule)); };
        java.encodeURI = function(value, encoding) { return pyEncodeURI(String(value), encoding || 'utf-8'); };
        """
    )
    for key, value in {
        "source": {
            "key": evaluator.bS.get("bookSourceUrl", ""),
            "bookSourceUrl": evaluator.bS.get("bookSourceUrl", ""),
            "bookSourceName": evaluator.bS.get("bookSourceName", ""),
        },
        "book": evaluator.bS.get("_tianshu_book", {}),
    }.items():
        evaluator.context.set("_tianshu_context", json.dumps(value, ensure_ascii=False))
        evaluator.context.eval(f"globalThis.{key} = JSON.parse(_tianshu_context)")
    evaluator.context.eval("source.getKey = function() { return source.key; };")
