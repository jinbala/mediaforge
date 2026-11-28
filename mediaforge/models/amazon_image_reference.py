import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

AmazonImageRole = Literal["main", "scene", "feature", "closeup"]
IMAGE_HOSTS = {"m.media-amazon.com", "images-na.ssl-images-amazon.com"}


def amazon_image_url(value: object) -> str | None:
    """Keep only public Amazon product images, for both collection and generation."""
    if not isinstance(value, str):
        return None
    url = value.strip()
    if url.startswith("//"):
        url = f"https:{url}"
    if len(url) > 2000 or re.search(r"[\s\\\x00-\x1f\x7f]", url):
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if (parsed.scheme == "https" and parsed.netloc.lower() in IMAGE_HOSTS
            and re.fullmatch(r"/images/I/[^/]+", parsed.path)):
        return url
    return None


class AmazonImageReference(BaseModel):
    image_url: str
    direction: str = Field(default="", max_length=500)

    @field_validator("image_url")
    @classmethod
    def _validate_image_url(cls, value: str) -> str:
        if url := amazon_image_url(value):
            return url
        raise ValueError("参考图必须是 Amazon 商品图片地址")
