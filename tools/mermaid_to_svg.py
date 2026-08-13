#!/usr/bin/env python3
"""Convert ```mermaid graph blocks inside a markdown file into inline SVG.

Supports graph TB/BT/LR/RL/TD with node shapes [], (), [[]], {}, (()).
Edges: A --> B, A -->|label| B, A --- B, A -.-> B.
Subgraph grouping is rendered as a dashed bounding box.
Pure standard library, no external dependencies.

Usage:
    python tools/mermaid_to_svg.py docs/foo.md [docs/bar.md ...]
"""

import re
import sys
import xml.dom.minidom
from pathlib import Path

BOX_W = 170
BOX_H = 56
X_GAP = 34
Y_GAP = 62
MARGIN = 30

COLORS = {
    "purple": ("#EEEDFE", "#534AB7"),
    "teal": ("#E1F5EE", "#0F6E56"),
    "amber": ("#FAEEDA", "#854F0B"),
    "blue": ("#E6F1FB", "#185FA5"),
    "green": ("#EAF3DE", "#3B6D11"),
    "gray": ("#F1EFE8", "#5F5E5A"),
}

# shape order matters: cylinder [(...)] and double [[...]] must precede single [.]
SHAPE_RE = r"(\[\(.*?\)\]|\[\".*?\"\]|\[\[.*?\]\]|\[.*?\]|\(.*?\)|\{.*?\}|\(\(.*?\)\))"


def extract_label(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("[(") and raw.endswith(")]"):
        return raw[2:-2]
    if raw.startswith("[[") and raw.endswith("]]"):
        return raw[2:-2]
    if raw.startswith("((") and raw.endswith("))"):
        return raw[2:-2]
    if raw.startswith("[") and raw.endswith("]"):
        return raw[1:-1]
    if raw.startswith("(") and raw.endswith(")"):
        return raw[1:-1]
    if raw.startswith("{") and raw.endswith("}"):
        return raw[1:-1]
    return raw


def classify(nid: str, label: str) -> str:
    s = (nid + " " + label).lower()
    if any(
        k in s
        for k in [
            "ec2",
            "app",
            "rag_demo",
            "vllm",
            "llm",
            "kserve",
            "gpu",
            "节点",
            "compute",
        ]
    ):
        return "purple"
    if any(
        k in s
        for k in [
            "opensearch",
            "aurora",
            "dynamodb",
            "s3",
            "redis",
            "faiss",
            "vdb",
            "hist",
            "aur",
            "cache",
            "vector",
            "pgvector",
            "数据库",
            "向量",
            "store",
        ]
    ):
        return "teal"
    if any(
        k in s
        for k in [
            "keycloak",
            "secret",
            "auth",
            "lambda",
            "api",
            "cognito",
            "用户",
            "iam",
            "identity",
        ]
    ):
        return "amber"
    if any(
        k in s
        for k in [
            "alb",
            "nlb",
            "cloudwatch",
            "ecr",
            "waf",
            "xray",
            "监控",
            "负载",
            "gateway",
        ]
    ):
        return "blue"
    return "gray"


def parse(text: str):
    nodes = {}
    edges = []
    subgraphs = []
    cur_sg = None
    for line in text.splitlines():
        st = line.strip()
        if not st:
            continue
        if re.match(r"(graph|flowchart)\s+(TB|BT|LR|RL|TD)", st):
            continue
        if st.startswith("%%"):
            continue
        if st.startswith("subgraph"):
            name = st[len("subgraph") :].strip()
            lbl = name
            mm = re.match(r'^(\w+)\s*\[?"?([^"\]]*)"?\]?$', name)
            if mm and mm.group(2):
                lbl = mm.group(2)
            elif mm:
                lbl = mm.group(1)
            cur_sg = {"label": lbl, "nodes": set()}
            subgraphs.append(cur_sg)
            continue
        if st == "end":
            cur_sg = None
            continue
        em = re.match(
            r"^(\w+)(?:\s*" + SHAPE_RE + r")?\s*"
            r"(-->|---|-\.->)\s*(?:\|([^|]*)\|)?\s*"
            r"(\w+)(?:\s*" + SHAPE_RE + r")?$",
            st,
        )
        if em:
            sid, slabel, _op, elabel, did, dlabel = em.groups()
            nodes.setdefault(sid, extract_label(slabel) if slabel else sid)
            nodes.setdefault(did, extract_label(dlabel) if dlabel else did)
            edges.append((sid, did, elabel or ""))
            if cur_sg is not None:
                cur_sg["nodes"].add(sid)
                cur_sg["nodes"].add(did)
            continue
        if ";" in st and not re.search(r"(-->|---|-\.->)", st):
            for part in st.split(";"):
                pid = part.strip()
                if pid and re.match(r"^\w+$", pid):
                    nodes.setdefault(pid, pid)
                    if cur_sg is not None:
                        cur_sg["nodes"].add(pid)
            continue
        nm = re.match(r"^(\w+)\s*" + SHAPE_RE + r"$", st)
        if nm:
            nodes.setdefault(nm.group(1), extract_label(nm.group(2)))
            if cur_sg is not None:
                cur_sg["nodes"].add(nm.group(1))
    return nodes, edges, subgraphs


def layout(nodes, edges):
    radj = {n: [] for n in nodes}
    for s, d, _ in edges:
        if s in nodes and d in nodes:
            radj[d].append(s)
    visited = {}

    def dfs(n):
        if n in visited:
            return visited[n]
        visited[n] = 0
        best = 0
        for p in radj[n]:
            best = max(best, dfs(p) + 1)
        visited[n] = best
        return best

    for n in nodes:
        dfs(n)
    max_layer = max(visited.values()) if visited else 0
    layers = {}
    order = list(nodes.keys())
    for n, l in visited.items():
        layers.setdefault(l, []).append(n)
    for l, val in layers.items():
        val.sort(key=lambda x: order.index(x))
    return layers, max_layer


def wrap_label(label: str):
    label = label.replace("\\n", "\n")
    if "\n" in label:
        return [p.strip() for p in label.split("\n")][:2]
    if len(label) > 15 and " " in label:
        parts = label.split(" ")
        mid = len(parts) // 2
        return [" ".join(parts[:mid]), " ".join(parts[mid:])]
    return [label]


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_svg(text: str):
    nodes, edges, subgraphs = parse(text)
    if not nodes:
        return None
    layers, max_layer = layout(nodes, edges)
    per_layer = [len(layers.get(l, [])) for l in range(max_layer + 1)]
    max_count = max(per_layer) if per_layer else 1
    width = MARGIN * 2 + max_count * (BOX_W + X_GAP) - X_GAP
    height = MARGIN * 2 + (max_layer + 1) * (BOX_H + Y_GAP) - Y_GAP
    pos = {}
    for l in range(max_layer + 1):
        row = layers.get(l, [])
        cnt = len(row)
        start_x = MARGIN + (max_count - cnt) / 2 * (BOX_W + X_GAP)
        y = MARGIN + l * (BOX_H + Y_GAP)
        for i, nid in enumerate(row):
            x = start_x + i * (BOX_W + X_GAP)
            pos[nid] = (x, y)

    out = [
        f'<svg viewBox="0 0 {width:.0f} {height:.0f}" width="100%" xmlns="http://www.w3.org/2000/svg" role="img">'
    ]
    out.append("<title>架构图</title>")
    out.append(
        '<defs><marker id="arw" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" '
        'markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" '
        'stroke="#5F5E5A" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
        "</marker></defs>"
    )

    for sg in subgraphs:
        ids = [n for n in sg["nodes"] if n in pos]
        if not ids:
            continue
        xs = [pos[n][0] for n in ids]
        ys = [pos[n][1] for n in ids]
        bx = min(xs) - 16
        by = min(ys) - 16
        bw = max(xs) + BOX_W - bx + 16
        bh = max(ys) + BOX_H - by + 16
        out.append(
            f'<rect x="{bx:.0f}" y="{by:.0f}" width="{bw:.0f}" height="{bh:.0f}" rx="18" '
            f'fill="#EEEDFE" fill-opacity="0.16" stroke="#534AB7" stroke-width="1" stroke-dasharray="6 4"/>'
        )
        out.append(
            f'<text x="{bx + 12:.0f}" y="{by + 16:.0f}" style="font:500 12px sans-serif; fill:#534AB7">{esc(sg["label"])}</text>'
        )

    for s, d, elabel in edges:
        if s not in pos or d not in pos:
            continue
        xs, ys = pos[s]
        xd, yd = pos[d]
        cx_s = xs + BOX_W / 2
        cx_d = xd + BOX_W / 2
        y_b = ys + BOX_H
        y_t = yd
        midY = (y_b + y_t) / 2
        path = f"M{cx_s:.0f} {y_b:.0f} V{midY:.0f} H{cx_d:.0f} V{y_t:.0f}"
        out.append(
            f'<path d="{path}" fill="none" stroke="#5F5E5A" stroke-width="1.4" marker-end="url(#arw)"/>'
        )
        if elabel:
            out.append(
                f'<text x="{(cx_s + cx_d) / 2:.0f}" y="{midY - 4:.0f}" text-anchor="middle" '
                f'style="font:400 11px sans-serif; fill:#5F5E5A">{esc(elabel)}</text>'
            )

    for nid, (x, y) in pos.items():
        label = nodes[nid]
        fill, stroke = COLORS[classify(nid, label)]
        lines = wrap_label(label)
        out.append(
            f'<g><rect x="{x:.0f}" y="{y:.0f}" width="{BOX_W}" height="{BOX_H}" rx="8" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>'
        )
        if len(lines) == 1:
            out.append(
                f'<text x="{x + BOX_W / 2:.0f}" y="{y + BOX_H / 2:.0f}" text-anchor="middle" '
                f'dominant-baseline="central" style="font:500 13px sans-serif; fill:#2C2C2A">{esc(lines[0])}</text>'
            )
        else:
            out.append(
                f'<text x="{x + BOX_W / 2:.0f}" y="{y + BOX_H / 2 - 9:.0f}" text-anchor="middle" '
                f'style="font:500 13px sans-serif; fill:#2C2C2A">{esc(lines[0])}</text>'
            )
            out.append(
                f'<text x="{x + BOX_W / 2:.0f}" y="{y + BOX_H / 2 + 11:.0f}" text-anchor="middle" '
                f'style="font:400 12px sans-serif; fill:#5F5E5A">{esc(lines[1])}</text>'
            )
        out.append("</g>")
    out.append("</svg>")
    return "\n".join(out)


def convert_file(path: Path):
    text = path.read_text(encoding="utf-8")
    blocks = re.findall(r"```mermaid\n(.*?)\n```", text, re.DOTALL)
    if not blocks:
        return 0

    def repl(m):
        svg = build_svg(m.group(1))
        return svg if svg else m.group(0)

    new_text, n = re.subn(r"```mermaid\n(.*?)\n```", repl, text, flags=re.DOTALL)
    if n:
        # validate every produced <svg> is well-formed XML
        for sv in re.findall(r"(<svg[\s\S]*?</svg>)", new_text):
            xml.dom.minidom.parseString(sv)
        path.write_text(new_text, encoding="utf-8")
    return n


if __name__ == "__main__":
    total = 0
    for p in sys.argv[1:]:
        f = Path(p)
        n = convert_file(f)
        print(f"{p}: {n} mermaid block(s) converted")
        total += n
    print(f"TOTAL: {total}")
