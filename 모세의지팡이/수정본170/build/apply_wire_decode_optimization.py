"""Replace copying parser internals, retaining the exact Wire v2 layout."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
path=ROOT/'Part1/program/staff_schema.py'
text=path.read_bytes().decode('utf-8').replace('\r\n','\n')
start=text.index('def read_v2(');end=text.index('def generated_mqh(',start)
old=text[start:end]
validation=old[old.index('    magic, version, seq, ns, nt, rows, cols, schema, kind ='):old.index('    payload = read_exact(size)')]
validation=validation.replace('    magic, version, seq, ns, nt, rows, cols, schema, kind = WIRE_HEADER.unpack(header)',
    "    if len(header)!=WIRE_HEADER.size:raise WireError('truncated v2 header')\n    fields=WIRE_HEADER.unpack(header)\n    magic, version, seq, ns, nt, rows, cols, schema, kind = fields")
payload=old[old.index('    if (zlib.crc32(payload)'):old.index('\n\ndef decode_v2(raw):')]
payload=payload.replace("payload[:ns].decode('utf-8', errors='strict')","bytes(payload[:ns]).decode('utf-8', errors='strict')")
payload=payload.replace("payload[ns:ns+nt].decode('utf-8', errors='strict')","bytes(payload[ns:ns+nt]).decode('utf-8', errors='strict')")
payload=payload.replace('decode_v2(payload[offset:offset+length])','_decode_view(payload[offset:offset+length])')
replacement='''def _v2_size(header):
'''+validation+'''    return fields,size


def immutable_byte_view(raw):
    view=memoryview(raw)
    owner=view.obj
    while isinstance(owner,memoryview):owner=owner.obj
    # A readonly flag over a bytearray/ndarray does not confer ownership.
    if not isinstance(owner,bytes):view=memoryview(bytes(view))
    return view.cast('B')


def _decode_payload(fields,payload,crc):
    magic,version,seq,ns,nt,rows,cols,schema,kind=fields
'''+payload+'''

def _decode_view(view):
    fields,size=_v2_size(view[:WIRE_HEADER.size])
    if len(view)!=WIRE_HEADER.size+size+4:
        raise WireError('truncated v2 frame' if len(view)<WIRE_HEADER.size+size+4 else 'v2 trailing bytes')
    return _decode_payload(fields,view[WIRE_HEADER.size:-4],struct.unpack_from('<I',view,len(view)-4)[0])


def read_v2(header, read_exact):
    fields,size=_v2_size(header)
    payload=read_exact(size);trailer=read_exact(4)
    if len(payload)!=size or len(trailer)!=4:raise WireError('truncated v2 frame')
    return _decode_payload(fields,immutable_byte_view(payload),struct.unpack('<I',trailer)[0])


def decode_v2(raw):
    return _decode_view(immutable_byte_view(raw))


'''
text=text[:start]+replacement+text[end:]
text=text.replace("body = struct.pack('<qI', sent_at_ms, len(frames))", "parts = [struct.pack('<qI', sent_at_ms, len(frames))]")
text=text.replace("        body += struct.pack('<I', len(raw)) + raw\n    return _wire_packet(seq, WIRE_BUNDLE", "        parts.extend((struct.pack('<I', len(raw)), raw))\n    body=b''.join(parts)\n    return _wire_packet(seq, WIRE_BUNDLE")
path.write_bytes(text.replace('\n','\r\n').encode('utf-8'))
