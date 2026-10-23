"""Build a portable openly licensed font; retain the approved GIF and M resources."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct

from PIL import Image, ImageDraw, ImageFont

NAMES = ('working.gif', 'warning.gif', 'done.gif', 'm.rle', 'font.bin')
EXPECTED = {
    'working.gif': '34dd49fb2891a7f979d9f892a80deae7ebb9877cf0c751adfda0281a9a4e0fd3',
    'warning.gif': 'de9324f72fa4cd2155521bf55c2e1917830c56a79cd49808b27729cb86244738',
    'done.gif': 'fc6eb7d37279896162f955131a7c50532aa9f3e34d08c4df8b0a467714627fd0',
    'm.rle': '94f70d00c530ab933c3575b7b1a50d51d1d9857675fd10ca26b64404928b9b41',
}


def verify_design(root):
    for name, expected in EXPECTED.items():
        path = root/'data'/name
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Design resource hash mismatch: '+name)
        if name.endswith('.gif'):
            with Image.open(path) as image:
                if image.size != (240, 240) or image.n_frames != 48:
                    raise ValueError('Invalid GIF size/frame count: '+name)
                for index in range(image.n_frames):
                    image.seek(index)
                    if image.info.get('duration') != 160 or image.disposal_method not in (0, 1, 2):
                        raise ValueError('Unsupported GIF timing/disposal: '+name)


def generate_manifest(root):
    manifest = {}
    for name in NAMES:
        data = (root/'data'/name).read_bytes()
        manifest[name] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    total = sum(entry['bytes'] for entry in manifest.values())
    if total >= 0x1FA000:
        raise ValueError('Resources exceed filesystem capacity before filesystem overhead')
    entries = [' {"/'+name+'",'+str(manifest[name]['bytes'])+',"'+manifest[name]['sha256']+'"}' for name in NAMES]
    font = manifest['font.bin']
    header = ('#pragma once\nstruct Resource {const char* path;unsigned bytes;const char* sha;};\n'
              'static const Resource resources[]={\n'+',\n'.join(entries)+'\n};\n'
              'static constexpr unsigned FONT_BYTES='+str(font['bytes'])+';\n'
              'static const char FONT_SHA256[]="'+font['sha256']+'";\n')
    (root/'include').mkdir(parents=True, exist_ok=True)
    (root/'include/resource_manifest.h').write_text(header, encoding='ascii')
    manifest['total_bytes'] = total
    (root/'ASSET_MANIFEST.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')


def build_font(root, path):
    font = ImageFont.truetype(str(path), 14)
    chars = sorted(set(chr(x) for x in range(32, 127)) | set(chr(x) for x in range(0x410, 0x450)) | set('Ёё—°·…'))
    glyphs = []
    for char in chars:
        glyph = Image.new('1', (16, 17))
        ImageDraw.Draw(glyph).text((0, 13), char, font=font, fill=1, anchor='ls')
        bits = bytes(sum(int(glyph.getpixel((x, y))) << (7-x % 8) for x in range(group, group+8))
                     for y in range(17) for group in (0, 8))
        advance = min(16, max(3, math.ceil(font.getlength(char))))
        glyphs.append(struct.pack('<HB', ord(char), advance)+bits)
    (root/'data/font.bin').write_bytes(struct.pack('<4sHH', b'MMF1', len(chars), 17)+b''.join(glyphs))


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--font', type=Path, default=root.parent/'assets/fonts/NotoSans-Bold.ttf')
    args = parser.parse_args()
    verify_design(root)
    build_font(root, args.font)
    generate_manifest(root)
    print('Open font and five-file resource manifest generated; design GIF/M bytes preserved.')


if __name__ == '__main__':
    main()
