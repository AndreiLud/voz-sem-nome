"""Social card (Open Graph image) page, rendered from site/src/og/card.html.j2.

The dev server serves it at /__og/pt and /__og/en; a 1200x630 screenshot of each becomes
site/src/static/og/og-<lang>.png, which the build copies to /assets/og/.
"""
from __future__ import annotations

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from enps.export.site import CHIP_CODES
from enps.export.site_content import SRC_DIR, chips, load_sources


def render_card(lang: str) -> str:
    src = load_sources(SRC_DIR)
    env = Environment(loader=FileSystemLoader(SRC_DIR / "og"), autoescape=True,
                      undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
    s = src.strings[lang]
    return env.get_template("card.html.j2").render(
        lang=src.site["html_lang"][lang], s=s,
        comment_lang="en" if s["home"]["hero"]["example_translation"] else "pt-BR",
        host=str(src.settings["site"]["base_url"]).split("//", 1)[-1],
        comment=chips(s["home"]["hero"]["example_translation"] or s["home"]["hero"]["example_comment"],
                      CHIP_CODES),
    )
