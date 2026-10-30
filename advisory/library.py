from pathlib import Path

from django.conf import settings

from .management.commands.ingest_knowledge import parse_frontmatter

CROP_PREFIXES = {
    "tomato": "Tomato_",
    "potato": "Potato___",
    "maize": "Corn_(maize)___",
}
REQUIRED_KEYS = ("crop", "condition", "title", "publisher", "url")


def condition_label(crop, condition):
    prefix = CROP_PREFIXES.get(crop, "")
    label = condition[len(prefix):] if prefix and condition.startswith(prefix) else condition
    return label.replace("__", " ").replace("_", " ").strip()


def parse_sections(body):
    sections = [{"heading": "", "paragraphs": []}]
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("# "):
            continue
        if line.startswith("## "):
            sections.append({"heading": line[3:].strip(), "paragraphs": []})
        else:
            sections[-1]["paragraphs"].append(line)
    return [section for section in sections if section["paragraphs"]]


def load_documents():
    sources_dir = Path(settings.BASE_DIR) / "advisory" / "sources"
    documents = []
    for path in sorted(sources_dir.glob("*.md")):
        metadata, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        if not all(metadata.get(key) for key in REQUIRED_KEYS) or not body:
            continue
        if not metadata["url"].startswith("https://") or metadata["crop"] not in CROP_PREFIXES:
            continue
        sections = parse_sections(body)
        headings = [
            line.lstrip("#").strip() for line in body.splitlines() if line.startswith("#")
        ]
        documents.append({
            "source_id": path.stem,
            "crop": metadata["crop"],
            "condition": metadata["condition"],
            "label": condition_label(metadata["crop"], metadata["condition"]),
            "title": metadata["title"],
            "publisher": metadata["publisher"],
            "url": metadata["url"],
            "date": metadata.get("date", "").strip('"'),
            "headings": headings,
            "summary": sections[0]["paragraphs"][0] if sections else "",
            "sections": sections,
        })
    return sorted(documents, key=lambda d: (d["crop"], d["label"]))


def filter_documents(documents, crop="", condition="", query=""):
    query = query.strip().lower()
    results = []
    for document in documents:
        if crop and document["crop"] != crop:
            continue
        if condition and document["condition"] != condition:
            continue
        if query:
            haystack = " ".join([
                document["title"], document["label"], document["publisher"], document["crop"],
                " ".join(p for s in document["sections"] for p in s["paragraphs"]),
            ]).lower()
            if query not in haystack:
                continue
        results.append(document)
    return results
