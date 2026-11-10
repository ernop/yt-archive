"""ytarchive web UI — paste a URL, get the video + squash framesheet."""
from .add import add_html
from .creator import browse_creator_html, creator_html
from .home import home_html
from .html import STATIC_DIR
from .server import make_handler, serve
from .video import detail_html

__all__ = ["STATIC_DIR", "add_html", "browse_creator_html", "creator_html", "detail_html", "home_html",
           "make_handler", "serve"]
