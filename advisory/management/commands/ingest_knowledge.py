import os
import json
import hashlib
from pathlib import Path
import chromadb
from chromadb.utils import embedding_functions
from django.core.management.base import BaseCommand
from django.conf import settings

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
    help = 'Ingests markdown advisory sources into ChromaDB.'

    def handle(self, *args, **options):
        sources_dir = Path(settings.BASE_DIR) / 'advisory' / 'sources'
        if not sources_dir.exists():
            self.stdout.write(self.style.ERROR(f"Sources directory not found: {sources_dir}"))
            return

        client = chromadb.PersistentClient(path=str(settings.CHROMA_DB_DIR))
        # Use default embedding function explicitly to ensure consistency
        emb_fn = embedding_functions.DefaultEmbeddingFunction()
        collection = client.get_or_create_collection(
            name="agricultural_advisory",
            embedding_function=emb_fn
        )

        indexed_count = 0
        updated_count = 0
        unchanged_count = 0
        rejected_count = 0

        # Load valid classes
        valid_classes = {}
        for crop in ['tomato', 'potato', 'maize']:
            classes_file = Path(settings.BASE_DIR) / 'model_artifacts' / crop / f'{crop}_classes.json'
            if classes_file.exists():
                with open(classes_file, 'r') as f:
                    valid_classes[crop] = json.load(f)
            else:
                valid_classes[crop] = []

        for file_path in sources_dir.glob('*.md'):
            content = file_path.read_text(encoding='utf-8')
            metadata, body = parse_frontmatter(content)

            # Validate metadata
            required_keys = ['crop', 'condition', 'title', 'publisher', 'url']
            if not all(k in metadata for k in required_keys):
                self.stdout.write(self.style.WARNING(f"Rejected {file_path.name}: missing required metadata."))
                rejected_count += 1
                continue

            if not metadata['url'].startswith('https://'):
                self.stdout.write(self.style.WARNING(f"Rejected {file_path.name}: invalid URL."))
                rejected_count += 1
                continue

            crop = metadata['crop']
            condition = metadata['condition']

            if crop not in valid_classes or condition not in valid_classes[crop]:
                self.stdout.write(self.style.WARNING(f"Rejected {file_path.name}: invalid crop or condition."))
                rejected_count += 1
                continue

            if not body:
                self.stdout.write(self.style.WARNING(f"Rejected {file_path.name}: empty content."))
                rejected_count += 1
                continue

            source_id = file_path.stem
            content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()

            # Check idempotency
            existing = collection.get(where={"source_id": source_id})
            is_update = False

            if existing and existing['metadatas']:
                # See if the current hash is already indexed
                existing_hashes = set(m.get('source_hash') for m in existing['metadatas'])
                if content_hash in existing_hashes:
                    unchanged_count += 1
                    continue
                else:
                    is_update = True

            chunks = chunk_text(body)
            documents = []
            metadatas = []
            ids = []

            for idx, chunk in enumerate(chunks):
                documents.append(chunk)
                meta = {
                    'crop': crop,
                    'condition': condition,
                    'title': metadata['title'],
                    'publisher': metadata['publisher'],
                    'url': metadata['url'],
                    'date': metadata.get('date', ''),
                    'source_id': source_id,
                    'chunk_index': idx,
                    'source_hash': content_hash
                }
                metadatas.append(meta)
                # Versioned ID
                ids.append(f"{source_id}_{content_hash}_{idx}")

            if documents:
                try:
                    collection.add(
                        documents=documents,
                        metadatas=metadatas,
                        ids=ids
                    )

                    # Safely remove old obsolete chunks for this source
                    if is_update and existing and existing['ids']:
                        old_ids = [old_id for old_id in existing['ids'] if content_hash not in old_id]
                        if old_ids:
                            collection.delete(ids=old_ids)

                    if is_update:
                        updated_count += 1
                    else:
                        indexed_count += 1
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Failed to ingest {file_path.name}: {e}"))
                    rejected_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"Ingestion complete.\n"
            f"New sources indexed: {indexed_count}\n"
            f"Sources updated: {updated_count}\n"
            f"Sources unchanged: {unchanged_count}\n"
            f"Sources rejected: {rejected_count}"
        ))
