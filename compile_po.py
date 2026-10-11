import polib
import os

locales = ['am', 'sw']

for lang in locales:
    dir_path = f'locale/{lang}/LC_MESSAGES'
    po_path = f'{dir_path}/django.po'
    mo_path = f'{dir_path}/django.mo'

    if os.path.exists(po_path):
        po = polib.pofile(po_path)
        po.save_as_mofile(mo_path)
        print(f"Compiled {po_path} to {mo_path}")
    else:
        print(f"File not found: {po_path}")
