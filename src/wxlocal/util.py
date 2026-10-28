"""Small, dependency-free serialization helpers shared by index and archive code."""
import hashlib
import json


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()
