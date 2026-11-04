from .knowledge import eligible_versions

CROP_PREFIXES = {
    "tomato": "Tomato_",
    "potato": "Potato___",
    "maize": "Corn_(maize)___",
}
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


def _prototype_documents(language):
    from .prototype import GENERAL_CROP, prototype_versions
    from .prototype import parse_sections as parse_prototype_sections

    documents = []
    for version in prototype_versions().filter(metadata__language=language):
        meta, document = version.metadata, version.document
        sections = [
            {"heading": section["heading"], "paragraphs": [section["text"]]}
            for section in parse_prototype_sections(version.extracted_text)
        ]
        crop, condition = meta["crop"], meta["condition"]
        documents.append({
            "source_id": document.source_identity, "document_version_id": str(version.id),
            "version": version.version, "crop": crop, "condition": condition,
            "label": document.title if crop == GENERAL_CROP else condition_label(crop, condition),
            "title": document.title, "publisher": document.publisher,
            "url": meta["references"][0]["url"] if meta.get("references") else document.source_url,
            "date": "", "headings": [s["heading"] for s in sections],
            "summary": sections[0]["paragraphs"][0] if sections else "",
            "sections": sections, "evidence_tier": "educational_prototype",
        })
    return documents


def load_documents(language=None):
    from .prototype import prototype_mode_enabled

    documents = _prototype_documents(language or "en") if prototype_mode_enabled() else []
    for version in eligible_versions().filter(indexing_status="indexed"):
        document = version.document
        body = version.extracted_text
        sections = parse_sections(body)
        headings = [
            line.lstrip("#").strip() for line in body.splitlines() if line.startswith("#")
        ]
        for crop in document.crops:
            for condition in document.diseases:
                documents.append({
                    "source_id": document.source_identity or str(document.id),
                    "document_version_id": str(version.id), "version": version.version,
                    "crop": crop, "condition": condition,
                    "label": condition_label(crop, condition), "title": document.title,
                    "publisher": document.publisher, "url": document.source_url,
                    "date": str(document.publication_date or ""), "headings": headings,
                    "summary": sections[0]["paragraphs"][0] if sections else "",
                    "sections": sections,
                })
    return sorted(documents, key=lambda d: (d["crop"] != "all", d["crop"], d["label"]))


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
