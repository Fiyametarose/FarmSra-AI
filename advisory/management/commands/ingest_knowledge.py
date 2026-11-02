from django.core.management.base import BaseCommand

def parse_frontmatter(content):
    lines = content.splitlines()
    if not lines or lines[0].strip() != '---':
        return {}, content

    metadata = {}
    end_idx = 1
    for i in range(1, len(lines)):
        if lines[i].strip() == '---':
            end_idx = i
            break
        if ':' in lines[i]:
            key, val = lines[i].split(':', 1)
            metadata[key.strip()] = val.strip()

    body = '\n'.join(lines[end_idx+1:]).strip()
    return metadata, body

def chunk_text(text, max_words=100, overlap=20):
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i:i + max_words])
        chunks.append(chunk)
        i += max_words - overlap
    return chunks

class Command(BaseCommand):
    help = 'Indexes database-approved agricultural document versions into ChromaDB.'

    def handle(self, *args, **options):
        from advisory.knowledge import eligible_versions, index_document_version
        succeeded = failed = 0
        for version in eligible_versions():
            if index_document_version(version):
                succeeded += 1
            else:
                failed += 1
        self.stdout.write(self.style.SUCCESS(
            f"Governed indexing complete: {succeeded} indexed, {failed} failed."
        ))
