"""On-disk archive format: SQLCipher snapshots, messages, media assets and documents.

Entry points:
    python -m wxlocal.archive.pipeline [--offline] [--resume DIR]   bulk export
    python -m wxlocal.archive.audit DIR                             independent audit
    python documents.py DIR                                         (document runtime only)
    python emoji.py DIR                                             CDN emoji retrieval
    node decode_media.cjs JOBS RESULTS OUT                          local media codecs
"""
