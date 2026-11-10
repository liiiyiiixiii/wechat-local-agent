"""Validate the checksum chain of a fixture WAL without replaying it."""

import struct


def wal_info(path):
    data = path.read_bytes()
    if len(data) < 32:
        return {'header_checksum_valid': False}
    magic, _, page_size = struct.unpack_from('>III', data)
    endian = '<' if magic == 0x377F0682 else '>'

    def checksum(block, sums=(0, 0)):
        a, b = sums
        for x, y in struct.iter_unpack(endian + 'II', block):
            a = (a + x + b) & 0xFFFFFFFF
            b = (b + y + a) & 0xFFFFFFFF
        return a, b

    sums = checksum(data[:24])
    valid = sums == struct.unpack_from('>II', data, 24)
    pos, commits = 32, 0
    while valid and pos + 24 + page_size <= len(data):
        frame = data[pos : pos + 24 + page_size]
        sums = checksum(frame[:8] + frame[24:], sums)
        if frame[8:16] != data[16:24] or sums != struct.unpack_from('>II', frame, 16):
            break
        commits += int(frame[4:8] != bytes(4))
        pos += len(frame)
    return {
        'header_checksum_valid': valid,
        'commit_markers_in_prefix': commits,
        'scan_end': 'end' if pos == len(data) else 'invalid_or_incomplete_frame',
    }
