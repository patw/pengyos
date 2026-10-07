#!/usr/bin/env python3
"""Render an xdg-utils DocBook refentry (scripts/desc/*.xml) as plain text.

Upstream renders these with `xmlto txt` (DocBook XSL + a text browser), then
generate-help-script.awk splices the text into each script: the lines between
"Name" and "Description" become --help, the whole page --manual. Neither xmlto
nor a text browser is in Buildroot, and the scripts only need readable text.

usage: docbook2txt.py IN.xml OUT.txt
"""
import re
import sys
import textwrap
import xml.etree.ElementTree as ET

WIDTH = 76


def text(el) -> str:
    """All text inside el, whitespace collapsed; <replaceable> as its word."""
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def synopsis(cmd) -> str:
    def arg(el) -> str:
        if el.tag in ("group", "arg"):
            if el.tag == "group":
                inner = " | ".join(arg(c) for c in el)
            else:
                parts = [(el.text or "").strip()] + [arg(c) for c in el]
                inner = " ".join(filter(None, parts))
            choice = el.get("choice", "opt")
            s = {"req": "{ %s }", "plain": "%s"}.get(choice, "[%s]") % inner
            return s + (" ..." if el.get("rep") == "repeat" else "")
        return text(el)

    return " ".join(filter(None, [text(cmd.find("command"))] +
                           [arg(c) for c in cmd if c.tag != "command"]))


def block(el, indent="   ") -> list[str]:
    """Paragraph-ish children of a section as wrapped, indented lines."""
    out: list[str] = []
    for child in el:
        if child.tag == "title":
            continue
        if child.tag in ("para", "simpara"):
            out += textwrap.wrap(text(child), WIDTH, initial_indent=indent,
                                 subsequent_indent=indent) + [""]
        elif child.tag in ("programlisting", "screen", "literallayout"):
            body = "".join(child.itertext()).strip("\n")
            out += [indent + line for line in body.splitlines()] + [""]
        elif child.tag == "variablelist":
            for entry in child.findall("varlistentry"):
                terms = (text(t) for t in entry.findall("term"))
                out.append(indent + ", ".join(terms))
                out += block(entry.find("listitem"), indent + "    ")
        elif child.tag in ("itemizedlist", "orderedlist"):
            for item in child.findall("listitem"):
                lines = block(item, indent + "  ")
                if lines:
                    lines[0] = indent + "* " + lines[0].lstrip()
                out += lines
        else:
            out += block(child, indent) or (
                textwrap.wrap(text(child), WIDTH, initial_indent=indent,
                              subsequent_indent=indent) + [""])
    return out


def render(root) -> str:
    lines: list[str] = []
    name = root.find("refnamediv")
    title = f"{text(name.find('refname'))} - {text(name.find('refpurpose'))}"
    lines += ["Name", "", "   " + title, ""]
    syn = root.find("refsynopsisdiv")
    if syn is not None:
        lines += ["Synopsis", ""]
        lines += ["   " + synopsis(c) for c in syn.iter("cmdsynopsis")] + [""]
    for sect in root.iter("refsect1"):
        lines += [text(sect.find("title")), ""] + block(sect)
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    src, dst = sys.argv[1], sys.argv[2]
    xml = open(src, encoding="utf-8").read()
    # Drop the DOCTYPE (external DTD + empty internal subset).
    xml = re.sub(r"<!DOCTYPE.*?\]>", "", xml, flags=re.S)
    open(dst, "w", encoding="utf-8").write(render(ET.fromstring(xml)))


if __name__ == "__main__":
    main()
