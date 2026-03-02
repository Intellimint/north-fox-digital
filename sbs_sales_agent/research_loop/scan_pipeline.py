from __future__ import annotations

import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from ..config import AgentSettings
from .types import ScanFinding, WebsiteEvidence, validate_finding

ABS_LINK_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
META_DESC_RE = re.compile(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', re.IGNORECASE | re.DOTALL)
H1_RE = re.compile(r"<h1[^>]*>", re.IGNORECASE)
IMG_ALT_MISSING_RE = re.compile(r"<img(?![^>]*\balt=)[^>]*>", re.IGNORECASE)
FORM_RE = re.compile(r"<form[^>]*>", re.IGNORECASE)
CTA_RE = re.compile(r"\b(book|schedule|get started|contact us|call now|request quote|buy now|demo|free consultation|get a quote)\b", re.IGNORECASE)
HTTP_SRC_RE = re.compile(r'(?:src|href)=["\']http://(?!localhost|127\.)', re.IGNORECASE)
NOINDEX_RE = re.compile(r'<meta[^>]+name=["\']robots["\'][^>]+content=["\'][^"\']*noindex', re.IGNORECASE)
VIEWPORT_RE = re.compile(r'<meta[^>]+name=["\']viewport["\']', re.IGNORECASE)
PHONE_RE = re.compile(r'\b(?:\+1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b')
TESTIMONIAL_RE = re.compile(r'\b(review|testimonial|client said|customers love|rated [0-9]|stars)\b', re.IGNORECASE)
SCHEMA_RE = re.compile(r'application/ld\+json', re.IGNORECASE)
SITEMAP_RE = re.compile(r'<loc>(https?://[^<]+)</loc>', re.IGNORECASE)
CANONICAL_RE = re.compile(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']', re.IGNORECASE)
ANALYTICS_RE = re.compile(r'(gtag\(|ga\(|google-analytics|analytics\.js|_gaq|fbq\(|hotjar|intercomSettings|heap\.load|mixpanel\.init)', re.IGNORECASE)
SOCIAL_LINK_RE = re.compile(r'href=["\'][^"\']*(?:facebook\.com|twitter\.com|x\.com|linkedin\.com|instagram\.com|yelp\.com|tiktok\.com)[^"\']*["\']', re.IGNORECASE)
FAVICON_RE = re.compile(r'<link[^>]+rel=["\'](?:shortcut )?icon["\']', re.IGNORECASE)
LANG_ATTR_RE = re.compile(r'<html[^>]+lang=["\'][a-zA-Z\-]+["\']', re.IGNORECASE)
COOKIE_CONSENT_RE = re.compile(r'(cookie.*consent|cookieyes|onetrust|gdpr|ccpa|privacy.*banner)', re.IGNORECASE)
GENERATOR_RE = re.compile(r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE)
OG_TITLE_RE = re.compile(r'<meta[^>]+property=["\']og:title["\']', re.IGNORECASE)
OG_IMAGE_RE = re.compile(r'<meta[^>]+property=["\']og:image["\']', re.IGNORECASE)
INPUT_TYPE_RE = re.compile(r'<input[^>]+type=["\'](?:text|email|password|tel|search)["\']', re.IGNORECASE)
LABEL_RE = re.compile(r'<label[^>]*>', re.IGNORECASE)
LOCAL_BUSINESS_SCHEMA_RE = re.compile(r'"@type"\s*:\s*"LocalBusiness"', re.IGNORECASE)
CONTACT_LINK_RE = re.compile(r'href=["\'][^"\']*\bcontact\b[^"\']*["\']', re.IGNORECASE)
PRICING_KEYWORD_RE = re.compile(r'\b(pricing|price list|our rates|our fees|packages|how much|get a quote)\b', re.IGNORECASE)
LAZY_LOAD_RE = re.compile(r'loading=["\']lazy["\']', re.IGNORECASE)
SKIP_NAV_RE = re.compile(r'href=["\']#(?:skip|main|content|primary)', re.IGNORECASE)
IMG_TAG_RE = re.compile(r'<img[^>]+>', re.IGNORECASE)
WORD_CONTENT_RE = re.compile(r'\b[a-zA-Z]{3,}\b')
AUTOCOMPLETE_OFF_RE = re.compile(r'autocomplete=["\'](?:off|new-password)["\']', re.IGNORECASE)
PASSWORD_INPUT_RE = re.compile(r'<input[^>]+type=["\']password["\']', re.IGNORECASE)
META_REFRESH_RE = re.compile(r'<meta[^>]+http-equiv=["\']refresh["\']', re.IGNORECASE)
H1_CONTENT_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
H2_RE = re.compile(r"<h2[^>]*>", re.IGNORECASE)
H3_RE = re.compile(r"<h3[^>]*>", re.IGNORECASE)
GENERIC_H1_RE = re.compile(r"^(welcome|home|about|about us|services|contact|contact us|hello|hi|main|our services|get started|page not found)$", re.IGNORECASE)
TEL_LINK_RE = re.compile(r'href=["\']tel:', re.IGNORECASE)
COPYRIGHT_YEAR_RE = re.compile(r'(?:©|&copy;|\bcopyright\b)[^<]{0,80}', re.IGNORECASE)
CHAT_WIDGET_RE = re.compile(r'(?:intercom\.io|widget\.intercom\.io|drift\.com/assets|tawk\.to|livechatinc\.com|freshchat\.com|hubspot\.com/conversations-embed|crisp\.chat|tidio\.com|olark\.com)', re.IGNORECASE)
VIDEO_EMBED_RE = re.compile(r'(?:youtube\.com/embed|youtu\.be/|vimeo\.com/video|player\.vimeo\.com|jwplayer|brightcove\.net)', re.IGNORECASE)
GOOGLE_MAPS_EMBED_RE = re.compile(r'(?:maps\.google\.com|goo\.gl/maps|google\.com/maps|maps\.app\.goo\.gl)', re.IGNORECASE)
FORM_ACTION_HTTP_RE = re.compile(r'<form[^>]+action=["\']http://[^"\']+["\']', re.IGNORECASE)
LD_JSON_BLOCK_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.IGNORECASE | re.DOTALL)

# v15 additions
HEAD_SECTION_RE = re.compile(r'<head[^>]*>(.*?)</head>', re.IGNORECASE | re.DOTALL)
RENDER_BLOCKING_SCRIPT_RE = re.compile(r'<script(?![^>]*\b(?:async|defer)\b)[^>]+src=["\']', re.IGNORECASE)
ARIA_MAIN_RE = re.compile(r'role=["\']main["\']|<main[\s>]', re.IGNORECASE)
IMG_MISSING_DIMS_RE = re.compile(r'<img(?![^>]*\bwidth=)[^>]*>', re.IGNORECASE)

# v16 additions
H1_OPEN_RE = re.compile(r'<h1[^>]*>', re.IGNORECASE)
GOOGLE_FONTS_RE = re.compile(r'fonts\.googleapis\.com|fonts\.gstatic\.com', re.IGNORECASE)
PRECONNECT_RE = re.compile(r'<link[^>]+rel=["\'](?:preconnect|dns-prefetch)["\']', re.IGNORECASE)

# v17 additions
JQUERY_VERSION_RE = re.compile(r'jquery[./\-]v?(\d+)\.(\d+)[\._\-]', re.IGNORECASE)
EXTERNAL_SCRIPT_SRC_RE = re.compile(r'<script[^>]+src=["\']https?://([^/"\']+)', re.IGNORECASE)
IFRAME_RE = re.compile(r'<iframe[^>]*>', re.IGNORECASE)
IFRAME_TITLE_RE = re.compile(r'<iframe[^>]+title=["\'][^"\']+["\']', re.IGNORECASE)

# v18 additions
SERVER_DISCLOSURE_RE = re.compile(
    r'(?:Apache|nginx|PHP|ASP\.NET|IIS|Node\.js|Express|Werkzeug)[\/\s][\d]',
    re.IGNORECASE,
)
CDN_SCRIPT_TAG_RE = re.compile(r'<script[^>]+src=["\']https?://[^"\']+["\'][^>]*>', re.IGNORECASE)
INTEGRITY_ATTR_RE = re.compile(r'\bintegrity=["\']sha(?:256|384|512)-', re.IGNORECASE)

# v19 additions
CSP_UNSAFE_RE = re.compile(r'\bunsafe-(?:inline|eval)\b', re.IGNORECASE)
COOKIE_SECURITY_FLAG_RE = re.compile(r'\b(?:httponly|secure|samesite)\b', re.IGNORECASE)
CORS_WILDCARD_RE = re.compile(r'^\s*\*\s*$')

# v20 additions
OPEN_REDIRECT_RE = re.compile(
    r'href=["\'][^"\']*[?&](url|redirect|next|return|goto|dest)=https?',
    re.IGNORECASE,
)
REVIEW_SCHEMA_RE = re.compile(
    r'"@type"\s*:\s*"(?:Review|AggregateRating|Product)"',
    re.IGNORECASE,
)

# v21 additions
DEPRECATED_HTML_RE = re.compile(
    r'<(?:marquee|blink|font|center|strike|acronym|basefont)[^>]*>',
    re.IGNORECASE,
)
POSITIVE_TABINDEX_RE = re.compile(r'tabindex=["\']([1-9]\d*)["\']', re.IGNORECASE)
INLINE_STYLE_RE = re.compile(r'\bstyle=["\'][^"\']{3,}["\']', re.IGNORECASE)

# v23 additions
OG_DESC_RE = re.compile(r'<meta[^>]+property=["\']og:description["\']', re.IGNORECASE)
META_KEYWORDS_RE = re.compile(r'<meta[^>]+name=["\']keywords["\']', re.IGNORECASE)
TABLE_RE = re.compile(r'<table[^>]*>', re.IGNORECASE)
TH_ELEMENT_RE = re.compile(r'<th[\s>]', re.IGNORECASE)
AUTOPLAY_MEDIA_RE = re.compile(r'<(?:video|audio)[^>]*\bautoplay\b[^>]*>', re.IGNORECASE)
MUTED_ATTR_RE = re.compile(r'\bmuted\b', re.IGNORECASE)

# v24 additions
STYLE_BLOCK_RE = re.compile(r'<style[^>]*>(.*?)</style>', re.IGNORECASE | re.DOTALL)
FOCUS_OUTLINE_SUPPRESS_RE = re.compile(r'\boutline\s*:\s*(?:none|0(?:px|em|rem|pt)?\s*[;}\s])', re.IGNORECASE)
SUBMIT_ELEMENT_RE = re.compile(r'<(?:button|input)[^>]+type=["\']submit["\']', re.IGNORECASE)
LANG_ATTR_CAPTURE_RE = re.compile(r'<html[^>]+lang=["\']([a-zA-Z]{2,3}(?:-[a-zA-Z]{2,8})*)["\']', re.IGNORECASE)
CAROUSEL_RE = re.compile(
    r'(?:class=["\'][^"\']*(?:carousel|slider|swiper|slideshow)[^"\']*["\']'
    r'|data-ride=["\']carousel["\'])',
    re.IGNORECASE,
)
CAROUSEL_PAUSE_RE = re.compile(
    r'(?:data-pause=["\']hover["\']|\.pause\(\)|aria-label=["\']pause["\']'
    r'|class=["\'][^"\']*pause[^"\']*["\'])',
    re.IGNORECASE,
)
CAROUSEL_INTERVAL_RE = re.compile(r'data-interval=["\'](\d+)["\']|autoplay\s*:\s*\{', re.IGNORECASE)

# v25 additions
VIDEO_ELEMENT_RE = re.compile(r'<video\b[^>]*>', re.IGNORECASE)
TRACK_CAPTION_RE = re.compile(
    r'<track\b[^>]*\bkind=["\'](?:captions|subtitles|descriptions)["\']',
    re.IGNORECASE,
)
FORM_AUTOCOMPLETE_OFF_RE = re.compile(
    r'<form\b[^>]*\bautocomplete=["\']off["\']',
    re.IGNORECASE,
)
INPUT_AUTOCOMPLETE_OFF_RE = re.compile(
    r'<input\b[^>]*\bautocomplete=["\']off["\']',
    re.IGNORECASE,
)
PLACEHOLDER_INPUT_RE = re.compile(
    r'<input\b[^>]*\bplaceholder=["\'][^"\']+["\'][^>]*>',
    re.IGNORECASE,
)
LABEL_FOR_ID_RE = re.compile(
    r'<label\b[^>]*\bfor=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
INPUT_ID_RE = re.compile(r'\bid=["\']([^"\']+)["\']', re.IGNORECASE)
ARIA_LABEL_ATTR_RE = re.compile(r'\baria-label=["\']', re.IGNORECASE)
PDF_LINK_ANCHOR_RE = re.compile(
    r'<a\b[^>]*\bhref=["\'][^"\']*\.pdf["\'][^>]*>([^<]{0,120})</a>',
    re.IGNORECASE,
)
BREADCRUMB_NAV_RE = re.compile(
    r'(?:aria-label=["\'][^"\']*breadcrumb|class=["\'][^"\']*breadcrumb)',
    re.IGNORECASE,
)
BREADCRUMB_SCHEMA_RE = re.compile(r'BreadcrumbList', re.IGNORECASE)

# v26 additions
LEGACY_IMG_SRC_RE = re.compile(
    r'<img\b[^>]*\bsrc=["\'][^"\']*\.(?:jpg|jpeg|png)["\']',
    re.IGNORECASE,
)
PICTURE_ELEMENT_RE = re.compile(r'<picture\b', re.IGNORECASE)
ADDRESS_TEXT_RE = re.compile(
    r'\b\d{2,5}\s+[A-Za-z][A-Za-z\s]{2,30}(?:St\.?|Ave\.?|Blvd\.?|Dr\.?|Rd\.?|Way|Ln\.?|Ct\.?|Pl\.?|Suite?|Ste\.?)\b',
    re.IGNORECASE,
)
ADDRESS_ELEMENT_RE = re.compile(r'<address[\s>]', re.IGNORECASE)
POSTAL_ADDRESS_RE = re.compile(r'"@type"\s*:\s*"PostalAddress"', re.IGNORECASE)
FAQ_CONTENT_RE = re.compile(
    r'(?:<(?:details|summary)\b|class=["\'][^"\']*\bfaq\b[^"\']*["\']|\bfrequently\s+asked\b)',
    re.IGNORECASE,
)
FAQ_SCHEMA_RE = re.compile(r'"@type"\s*:\s*"FAQPage"', re.IGNORECASE)
TITLE_SEPARATOR_RE = re.compile(r'<title[^>]*>([^<]+)</title>', re.IGNORECASE)
PRIVACY_POLICY_LINK_RE = re.compile(
    r'href=["\'][^"\']*(?:privacy|gdpr|legal)[^"\']*["\']',
    re.IGNORECASE,
)

# v27 additions
VIEWPORT_SCALABLE_RE = re.compile(
    r'<meta[^>]+name=["\']viewport["\'][^>]*content=["\'][^"\']*(?:user-scalable\s*=\s*no|maximum-scale\s*=\s*1(?:[^0-9]|$))',
    re.IGNORECASE,
)
GA_TRACKING_ID_RE = re.compile(
    r'(?:G-[A-Z0-9]{6,12}|UA-\d{5,}-\d+)',
    re.IGNORECASE,
)
ALT_FILENAME_RE = re.compile(
    r'<img\b[^>]*\balt=["\']([^"\']*\.(?:jpg|jpeg|png|gif|webp|svg)'
    r'|(?:IMG|DSC|DCIM|pic|photo|image|img|file|scan)[_\-]\d{3,}'
    r'|[\d_\-]{3,})["\'][^>]*>',
    re.IGNORECASE,
)
FORM_METHOD_GET_RE = re.compile(
    r'<form\b[^>]*\bmethod=["\']get["\'][^>]*>',
    re.IGNORECASE,
)

# v28 additions
CSS_KEYFRAME_RE = re.compile(r'@keyframes\s+\w+', re.IGNORECASE)
REDUCED_MOTION_RE = re.compile(r'prefers-reduced-motion', re.IGNORECASE)
SOCIAL_SHARE_WIDGET_RE = re.compile(
    r'(?:addthis|sharethis|addToAny|data-sharer|share-this'
    r'|class=["\'][^"\']*\bshare-btn[^"\']*["\']'
    r'|href=["\'][^"\']*(?:sharer|share\?url=)[^"\']*["\'])',
    re.IGNORECASE,
)
EXTERNAL_DOMAIN_HREF_RE = re.compile(
    r'<link\b[^>]+href=["\']https?://([^/"\'>\s]+)',
    re.IGNORECASE,
)
RESOURCE_HINT_HREF_RE = re.compile(
    r'<link[^>]+rel=["\'](?:preconnect|dns-prefetch)["\'][^>]+href=["\']https?://([^/"\'>\s]+)',
    re.IGNORECASE,
)
ROBOTS_ASSET_DISALLOW_RE = re.compile(
    r'^Disallow:\s*/(?:css|js|javascript|images?|assets?|static|img|scripts?|styles?|wp-content)[/\s]*$',
    re.IGNORECASE | re.MULTILINE,
)

# v29 additions
HSTS_HEADER_RE = re.compile(r'max-age\s*=\s*(\d+)', re.IGNORECASE)
HSTS_SUBDOMAIN_RE = re.compile(r'includeSubDomains', re.IGNORECASE)
REFERRER_UNSAFE_VALUES = frozenset(["unsafe-url", "no-referrer-when-downgrade"])
SOFT_404_TEXT_RE = re.compile(
    r'(?:page\s+(?:not\s+found|does\s+not\s+exist|cannot\s+be\s+found|was\s+not\s+found)'
    r'|404\s+(?:error|not\s+found)|this\s+page\s+(?:no\s+longer\s+exists|doesn\'t\s+exist|has\s+been\s+removed)'
    r'|sorry[,\s]+we\s+(?:couldn\'t|can\'t|could\s+not)\s+find)',
    re.IGNORECASE,
)
WEBSITE_SCHEMA_RE = re.compile(r'"@type"\s*:\s*"WebSite"', re.IGNORECASE)
INLINE_EVENT_HANDLER_RE = re.compile(
    r'\bon(?:click|load|error|submit|mouseover|mouseout|focus|blur|change|input|keydown|keyup|keypress)\s*=["\']',
    re.IGNORECASE,
)

# v22 additions
GENERIC_ANCHOR_RE = re.compile(
    r'<a\b[^>]*>\s*(?:click\s+here|read\s+more|here|learn\s+more|more\s+info|details|more)\s*</a>',
    re.IGNORECASE,
)
BLANK_TARGET_RE = re.compile(r'<a\b[^>]*target=["\']_blank["\'][^>]*>', re.IGNORECASE)
NOOPENER_ATTR_RE = re.compile(r'rel=["\'][^"\']*noopener[^"\']*["\']', re.IGNORECASE)
INPUT_AUTOCOMPLETE_FIELD_RE = re.compile(
    r'<input[^>]+type=["\'](?:email|tel)["\'][^>]*>',
    re.IGNORECASE,
)
HAS_AUTOCOMPLETE_ATTR_RE = re.compile(r'\bautocomplete=["\']', re.IGNORECASE)

# v30 additions
FRAME_ANCESTORS_CSP_RE = re.compile(r'frame-ancestors\s+', re.IGNORECASE)
SELECT_ELEMENT_RE = re.compile(r'<select\b[^>]*>', re.IGNORECASE)
SELECT_LABEL_RE = re.compile(
    r'<label\b[^>]*\bfor=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
UNMIN_SCRIPT_RE = re.compile(
    r'<(?:script\b[^>]+\bsrc|link\b[^>]+\bhref)=["\']([^"\']+\.(?:js|css))["\']',
    re.IGNORECASE,
)

# v31 additions
RSS_LINK_RE = re.compile(r'<link[^>]+type=["\']application/rss\+xml["\']', re.IGNORECASE)
BLOG_NAV_HREF_RE = re.compile(
    r'href=["\'][^"\']*(?:/blog|/news|/articles|/resources|/insights)[/"\'?#]',
    re.IGNORECASE,
)
TWITTER_CARD_RE = re.compile(r'<meta[^>]+name=["\']twitter:card["\']', re.IGNORECASE)
NON_CDN_BODY_SCRIPT_RE = re.compile(
    r'<script(?![^>]*\b(?:async|defer)\b)[^>]+src=["\']'
    r'(?!(?:https?:)?//(?:cdnjs\.|ajax\.googleapis|code\.jquery|cdn\.jsdelivr|unpkg\.com'
    r'|use\.fontawesome|kit\.fontawesome|cdn\.bootcss|maxcdn|maps\.googleapis'
    r'|connect\.facebook|platform\.twitter|platform\.linkedin|assets\.pinterest'
    r'|cdn\.shopify|translate\.google|cdn\.webflow|static\.hotjar|widget\.intercom'
    r'|cdn\.segment|sentry\.io|js\.stripe|js\.braintreegateway|paypalobjects))',
    re.IGNORECASE,
)

# v32 additions
INPUT_TEXT_NAMED_RE = re.compile(
    r'<input\b[^>]*\btype=["\']text["\'][^>]*\bname=["\'][^"\']*(?:email|mail|phone|tel|mobile|cell|contact)[^"\']*["\']'
    r'|<input\b[^>]*\bname=["\'][^"\']*(?:email|mail|phone|tel|mobile|cell|contact)[^"\']*["\'][^>]*\btype=["\']text["\']',
    re.IGNORECASE,
)
H2_CONTENT_RE = re.compile(r'<h2[^>]*>(.*?)</h2>', re.IGNORECASE | re.DOTALL)
NAV_ELEMENT_RE = re.compile(r'<nav\b[^>]*>', re.IGNORECASE)
NAV_ARIA_LABEL_RE = re.compile(
    r'<nav\b[^>]*(?:aria-label|aria-labelledby)=["\']',
    re.IGNORECASE,
)
ROBOTS_NOFOLLOW_RE = re.compile(
    r'<meta\b[^>]*name=["\']robots["\'][^>]*content=["\'][^"\']*\bnofollow\b',
    re.IGNORECASE,
)

# v33 additions
# X-Content-Type-Options header — detects absence of nosniff directive
X_CONTENT_TYPE_OPTIONS_KEY = "x-content-type-options"
# Permissions-Policy header (and legacy Feature-Policy)
PERMISSIONS_POLICY_KEY_RE = re.compile(
    r'^(?:permissions-policy|feature-policy)$',
    re.IGNORECASE,
)
# og:image meta tag — social share preview image
OG_IMAGE_RE = re.compile(
    r'<meta\b[^>]*(?:property|name)=["\']og:image["\']',
    re.IGNORECASE,
)
# Detect broad link text-decoration suppression in style blocks (WCAG 1.4.1)
# Matches patterns like: a { text-decoration: none } or a:link { text-decoration:none }
LINK_NODECOR_RE = re.compile(
    r'\ba(?::link|:visited)?\s*\{[^}]*text-decoration\s*:\s*none',
    re.IGNORECASE,
)
# Hover restore — if :hover rule includes text-decoration other than none, the link IS distinguishable
LINK_HOVER_RESTORE_RE = re.compile(
    r'\ba:hover\s*\{[^}]*text-decoration\s*:\s*(?!none\b)\S',
    re.IGNORECASE,
)
# Empty-alt linked images — <a ...><img ... alt=""></a>
IMG_EMPTY_ALT_IN_LINK_RE = re.compile(
    r'<a\b[^>]*href[^>]*>\s*<img\b[^>]*\balt=["\']["\'][^>]*/?>',
    re.IGNORECASE,
)

# v34 additions
# Google Fonts link without display=swap — FOIT (Flash of Invisible Text) risk
FONT_DISPLAY_SWAP_RE = re.compile(
    r'<link\b[^>]*href=["\']https://fonts\.googleapis\.com/[^"\']*["\'][^>]*/?>',
    re.IGNORECASE,
)
FONT_DISPLAY_SWAP_PARAM_RE = re.compile(
    r'[?&]display=swap',
    re.IGNORECASE,
)
# Buttons without accessible name — no text, no aria-label, no title attr
BUTTON_OPEN_RE = re.compile(
    r'<button\b([^>]*)>(\s*)</button>',
    re.IGNORECASE | re.DOTALL,
)
# Price text patterns — common currency/pricing signals in page copy
PRICE_TEXT_RE = re.compile(
    r'(?:\$\s*\d[\d,]*(?:\.\d{2})?|\d+\s*/\s*(?:mo|month|yr|year)|per\s+month|starting\s+(?:at|from)\s+\$)',
    re.IGNORECASE,
)
# Offer / Product JSON-LD schema
OFFER_SCHEMA_RE = re.compile(
    r'"@type"\s*:\s*"(?:Offer|Product|AggregateOffer|PriceSpecification)"',
    re.IGNORECASE,
)
# Preload link tag for assets
PRELOAD_LINK_RE = re.compile(
    r'<link\b[^>]*\brel=["\']preload["\']',
    re.IGNORECASE,
)
# Set-Cookie header with session/auth/token named cookies
COOKIE_SESSION_NAME_RE = re.compile(
    r'(?:^|;\s*)(?:session|auth|login|token|user|account|sid|uid)[^=]*=',
    re.IGNORECASE,
)
COOKIE_SECURE_PREFIX_RE = re.compile(
    r'__(?:Secure|Host)-',
    re.IGNORECASE,
)
# v35 regex constants
# SPF lookup mechanisms that count against the 10-lookup limit (RFC 7208 §4.6.4)
SPF_LOOKUP_MECHANISM_RE = re.compile(
    r'\b(?:include:|a(?::|(?=\s|$))|mx(?::|(?=\s|$))|ptr(?::|(?=\s|$))|exists:|redirect=)',
    re.IGNORECASE,
)
# Title tag length check — used by _check_page_title_length
TITLE_CONTENT_RE = re.compile(r'<title[^>]*>(.*?)</title>', re.IGNORECASE | re.DOTALL)
# Apple touch icon — used by _check_apple_touch_icon_missing
APPLE_TOUCH_ICON_RE = re.compile(
    r'<link\b[^>]*rel=["\'][^"\']*apple-touch-icon[^"\']*["\']',
    re.IGNORECASE,
)
# Spam protection signals on pages (reCAPTCHA, hCaptcha, Cloudflare Turnstile)
SPAM_PROTECTION_RE = re.compile(
    r'(?:g-recaptcha|data-sitekey|hcaptcha|cf-turnstile|recaptcha\.net|\.grecaptcha|'
    r'data-callback=["\'](?:onSubmit|captchaCallback)|turnstile\.cloudflare)',
    re.IGNORECASE,
)
# Honeypot hidden-field signals (name contains bot/trap/honey/hide/dummy)
HONEYPOT_FIELD_RE = re.compile(
    r'<input\b[^>]*(?:name|id)=["\'][^"\']*(?:honey|bot|trap|dummy|fax|url|website)[^"\']*["\']',
    re.IGNORECASE,
)
# Google Fonts family name extraction
GOOGLE_FONT_FAMILY_RE = re.compile(
    r'fonts\.googleapis\.com/css[^"\']*family=([A-Za-z+:%0-9,|]+)',
    re.IGNORECASE,
)

# v36 additions
# Marketing pixel/analytics tracking scripts beyond standard GA4 — each adds render latency
TRACKING_PIXEL_RE = re.compile(
    r'(?:connect\.facebook\.net/[^"\']+/fbevents\.js'
    r'|hotjar\.com/c/hotjar-\d+'
    r'|clarity\.ms/tag/'
    r'|cdn\.heapanalytics\.com|heapanalytics\.com/js/'
    r'|cdn\.mixpanel\.com|mixpanel\.com/libs/'
    r'|cdn\.segment\.com|cdn\.segment\.io'
    r'|fullstory\.com/s/fs\.js'
    r'|static\.ads-twitter\.com|platform\.twitter\.com/oct\.js'
    r'|snap\.licdn\.com/analytics'
    r'|static\.criteo\.net|sb\.scorecardresearch\.com'
    r'|js\.intercomcdn\.com|widget\.drift\.com)',
    re.IGNORECASE,
)
# Raw email address pattern — used to detect addresses in HTML body
EMAIL_IN_BODY_RE = re.compile(
    r'\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b',
)
# Email inside a mailto: link — used to exclude from spam-harvesting check
EMAIL_IN_MAILTO_RE = re.compile(
    r'href=["\']mailto:[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}["\']',
    re.IGNORECASE,
)
# Organization JSON-LD schema type — separate from LocalBusiness/WebSite
ORGANIZATION_SCHEMA_RE = re.compile(
    r'"@type"\s*:\s*"Organization"',
    re.IGNORECASE,
)
# Sitemap directive in robots.txt — Google uses this for sitemap auto-discovery
ROBOTS_SITEMAP_DIRECTIVE_RE = re.compile(
    r'^Sitemap\s*:',
    re.IGNORECASE | re.MULTILINE,
)

# v37 additions
# Pagination rel=prev/next link tags — paginated archive page navigation signals
PAGINATION_REL_RE = re.compile(r'<link\b[^>]*\brel=["\'](?:prev|next)["\']', re.IGNORECASE)
# Article / BlogPosting / NewsArticle JSON-LD schema — structured data for content pages
ARTICLE_SCHEMA_RE = re.compile(
    r'"@type"\s*:\s*"(?:Article|NewsArticle|BlogPosting|TechArticle)"',
    re.IGNORECASE,
)
# Footer section opening tag — used to isolate footer contact info area
FOOTER_SECTION_RE = re.compile(r'<footer\b', re.IGNORECASE)
# Anchor hrefs pointing to on-page fragment IDs — e.g. href="#section-2"
ANCHOR_HREF_FRAGMENT_RE = re.compile(r'<a\b[^>]*\bhref=["\']#([^"\'>\s]+)["\']', re.IGNORECASE)
# External script src attributes — used to detect duplicate script loads
DUPLICATE_SCRIPT_RE = re.compile(r'<script\b[^>]*\bsrc=["\']([^"\']+)["\']', re.IGNORECASE)

# v38 additions
# Short/token image alt text — alt attributes with ≤2 visible chars that are meaningless
ALT_SHORT_TEXT_RE = re.compile(
    r'<img\b[^>]*\balt=["\']([^"\']{0,2})["\'][^>]*>',
    re.IGNORECASE,
)
# Keyword-stuffed heading — H1/H2 content with 3+ pipe/comma-separated keyword phrases
HEADING_KEYWORD_STUFF_RE = re.compile(
    r'(?:\|[^<|]{3,40}){3,}|(?:,[^<,]{3,40}){4,}',
    re.IGNORECASE,
)
# GA4 / UA tracking IDs already detected by GA_TRACKING_ID_RE —
# preconnect hint for google-analytics / googletagmanager domains
ANALYTICS_PRECONNECT_HINT_RE = re.compile(
    r'<link\b[^>]*rel=["\'](?:preconnect|dns-prefetch)["\'][^>]*href=["\'][^"\']*'
    r'(?:google-analytics\.com|googletagmanager\.com|analytics\.google\.com)["\']',
    re.IGNORECASE,
)
# Form required fields — inputs/textareas/selects with required attribute
REQUIRED_FIELD_RE = re.compile(
    r'<(?:input|textarea|select)\b[^>]*\brequired\b',
    re.IGNORECASE,
)
# ARIA live region or alert role — used for accessible error messages
ARIA_LIVE_RE = re.compile(
    r'role=["\']alert["\']|aria-live=["\'](?:assertive|polite)["\']|aria-errormessage=["\']',
    re.IGNORECASE,
)
# Meta charset declaration — <meta charset="..."> or <meta http-equiv="content-type" ...>
META_CHARSET_RE = re.compile(
    r'<meta\b[^>]*(?:\bcharset=["\'][^"\']+["\']|http-equiv=["\']content-type["\'])',
    re.IGNORECASE,
)

# v39 additions
# Skip navigation link — detects skip/jump-to-content anchor patterns via href or text content.
# Two alternatives: (1) href attribute contains "skip" (e.g. #skip-to-content, #skip-nav);
# (2) anchor text contains the word "skip" or a "jump to content/main" phrase.
SKIP_NAV_RE = re.compile(
    # Alternative 1: href contains "skip" — e.g. <a href="#skip-to-content">Skip</a>
    r'<a\b[^>]*\bhref=["\'][^"\']*skip[^"\']*["\'][^>]*>[^<]*</a>'
    r'|'
    # Alternative 2: link text contains skip or jump-to-content/bypass navigation phrase
    r'<a\b[^>]*>[^<]*\b(?:skip\b|jump\s+to\s+(?:content|main)|bypass\s+navigation)[^<]*</a>',
    re.IGNORECASE,
)
# External CSS link tags — <link rel="stylesheet" href="https://...">
EXTERNAL_CSS_LINK_RE = re.compile(
    r'<link\b[^>]*\brel=["\']stylesheet["\'][^>]*\bhref=["\']https?://[^"\']+["\'][^>]*>',
    re.IGNORECASE,
)
# Integrity attribute on link/script tags — SRI integrity=
CSS_INTEGRITY_ATTR_RE = re.compile(r'\bintegrity=["\'][^"\']+["\']', re.IGNORECASE)
# Fieldset grouping element — <fieldset
FIELDSET_LEGEND_RE = re.compile(r'<fieldset\b', re.IGNORECASE)
# HTML lang attribute present — <html ... lang=
LANG_ATTR_PRESENT_RE = re.compile(r'<html\b[^>]*\blang=["\'][^"\']+["\']', re.IGNORECASE)

# v40 additions
# Web app manifest link tag — <link rel="manifest" href="...">
MANIFEST_LINK_RE = re.compile(r'<link\b[^>]*\brel=["\']manifest["\'][^>]*>', re.IGNORECASE)
# hreflang annotation — <link rel="alternate" hreflang="...">
HREFLANG_RE = re.compile(r'<link\b[^>]*\bhreflang=["\'][^"\']+["\'][^>]*>', re.IGNORECASE)
# HTML element open tags — used as heuristic DOM size estimator
HTML_ELEMENT_RE = re.compile(r'<[a-z][a-z0-9]*[\s>]', re.IGNORECASE)
# Phone/zip named text inputs without semantic type or pattern — causes missed mobile UX
PHONE_ZIP_INPUT_RE = re.compile(
    r'<input\b[^>]*(?:name|id|placeholder)=["\'][^"\']*(?:phone|mobile|zip|postal|postcode|fax)[^"\']*["\'][^>]*>',
    re.IGNORECASE,
)
# Semantic tel/number type or pattern attr — excludes well-typed inputs from the check
SEMANTIC_INPUT_TYPE_RE = re.compile(
    r'<input\b[^>]*\btype=["\'](?:tel|number)["\']|<input\b[^>]*\bpattern=["\'][^"\']+["\']',
    re.IGNORECASE,
)

# v41 additions
# SVG elements — all SVG open tags; used to count total SVGs for aria coverage check
SVG_OPEN_RE = re.compile(r'<svg\b[^>]*>', re.IGNORECASE)
# SVGs marked as decorative — aria-hidden=true or role=presentation/none
SVG_ARIA_HIDDEN_RE = re.compile(
    r'<svg\b[^>]*(?:aria-hidden=["\']true["\']|role=["\'](?:presentation|none)["\'])[^>]*>',
    re.IGNORECASE,
)
# SVGs with explicit img role — meaningful graphics with accessible labels
SVG_ROLE_IMG_RE = re.compile(r'<svg\b[^>]*\brole=["\']img["\'][^>]*>', re.IGNORECASE)
# Back-to-top navigation anchor/button — reduces UX friction on long-scroll pages
BACK_TO_TOP_RE = re.compile(
    r'(?:href=["\']#(?:top|back-to-top|scroll-top|goto-top|page-top)["\']'
    r'|class=["\'][^"\']*(?:back-to-top|scroll-to-top|back2top|totop)[^"\']*["\']'
    r'|id=["\'](?:back-to-top|scroll-top|top-button|back-top)["\'])',
    re.IGNORECASE,
)
# Iframe with sandbox attribute — restricts embedded content permissions
IFRAME_SANDBOX_RE = re.compile(r'<iframe\b[^>]*\bsandbox=["\'][^"\']*["\']', re.IGNORECASE)
# External iframe — loads content from an absolute external origin URL
IFRAME_EXTERNAL_SRC_RE = re.compile(r'<iframe\b[^>]*\bsrc=["\']https?://', re.IGNORECASE)

# Common DKIM selectors — checked in order; most ESP defaults first
_DKIM_SELECTORS = [
    "default",
    "google",
    "k1",
    "k2",
    "mail",
    "smtp",
    "email",
    "selector1",
    "selector2",
    "mandrill",
    "sendgrid",
    "mailchimp",
]

# Inner-page path prefixes that are worth crawling for additional findings
_INNER_PAGE_PREFIXES = (
    "/about",
    "/services",
    "/contact",
    "/team",
    "/faq",
    "/blog",
    "/pricing",
    "/location",
    "/locations",
    "/portfolio",
    "/products",
    "/our-work",
    "/gallery",
)

_UNVERIFIED_CLAIM_PATTERNS = [
    re.compile(r"\b(?:studies show|typically|on average|industry benchmark|benchmarks?)\b", re.IGNORECASE),
    re.compile(r"\bGoogle penalizes\b", re.IGNORECASE),
    re.compile(r"\d{1,3}(?:\s*[–-]\s*\d{1,3})?\s*%"),
    re.compile(r"\$\d[\d,]*(?:\s*[–-]\s*\$?\d[\d,]*)?"),
]


def _finding_to_dict(finding: Any) -> dict[str, Any]:
    if is_dataclass(finding):
        return dict(asdict(finding))
    if isinstance(finding, dict):
        return dict(finding)
    return {"value": str(finding)}


def _clean_text(value: str, *, max_len: int = 220) -> str:
    txt = re.sub(r"<[^>]+>", " ", value or "")
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:max_len]


def _strip_unverified_claims(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return raw
    sentences = re.split(r"(?<=[.!?])\s+", raw)
    kept: list[str] = []
    for s in sentences:
        if not s.strip():
            continue
        if any(p.search(s) for p in _UNVERIFIED_CLAIM_PATTERNS):
            continue
        kept.append(s.strip())
    cleaned = " ".join(kept).strip()
    return cleaned if cleaned else raw


def _sanitize_findings(findings: list[ScanFinding]) -> None:
    for f in findings:
        f.description = _strip_unverified_claims(f.description)
        f.remediation = _strip_unverified_claims(f.remediation)


def _make_solid_color_png(path: Path, width: int = 320, height: int = 180, color: tuple[int, int, int] = (232, 244, 255)) -> None:
    """Create a solid-color PNG using only stdlib (no external deps)."""
    import struct
    import zlib

    r, g, b = color
    row = b'\x00' + bytes([r, g, b]) * width
    raw_data = row * height
    compressed = zlib.compress(raw_data, 6)

    def _chunk(name: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(name + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", crc)

    png = (
        b'\x89PNG\r\n\x1a\n'
        + _chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
        + _chunk(b'IDAT', compressed)
        + _chunk(b'IEND', b'')
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def _norm_url(website: str) -> str:
    w = website.strip()
    if not w.startswith(("http://", "https://")):
        w = f"https://{w}"
    return w


def _capture_placeholder_screenshot(path: Path, title: str, subtitle: str) -> str:
    try:
        import matplotlib.pyplot as plt  # type: ignore
        import matplotlib  # type: ignore
        matplotlib.rcParams.update({"font.family": "DejaVu Sans"})

        fig, ax = plt.subplots(figsize=(10, 5.5))
        ax.set_facecolor("#f0f5fb")
        fig.patch.set_facecolor("#f0f5fb")
        ax.axis("off")
        ax.text(0.02, 0.90, title[:120], fontsize=16, fontweight="bold", color="#13233a")
        ax.text(0.02, 0.70, subtitle[:400], fontsize=10, color="#3a4f6a", wrap=True)
        ax.text(0.02, 0.08, "⚠ Screenshot capture unavailable — placeholder shown", fontsize=9, color="#888")
        fig.tight_layout()
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=140)
        plt.close(fig)
    except Exception:
        # Pure-stdlib PNG fallback — always produces a valid image file
        _make_solid_color_png(path, width=960, height=540, color=(240, 245, 251))
    return str(path)


_AXE_IMPACT_TO_SEVERITY: dict[str, str] = {
    "critical": "critical",
    "serious": "high",
    "moderate": "medium",
    "minor": "low",
}


def _axe_violations_to_findings(violations: list[dict], page_url: str, shot_map: dict[str, str]) -> list[ScanFinding]:
    """Convert axe-core violation dicts into ScanFinding objects."""
    out: list[ScanFinding] = []
    seen_ids: set[str] = set()
    for v in violations:
        rule_id = str(v.get("id") or "")
        if not rule_id or rule_id in seen_ids:
            continue
        seen_ids.add(rule_id)
        impact = str(v.get("impact") or "minor")
        severity = _AXE_IMPACT_TO_SEVERITY.get(impact, "low")
        help_text = str(v.get("help") or v.get("description") or "Accessibility issue detected")
        description = str(v.get("description") or help_text)
        nodes = v.get("nodes") or []
        snippet: str | None = None
        if nodes:
            raw = str(nodes[0].get("html") or "").strip()
            if raw:
                snippet = raw[:200]
        remediation = ""
        if nodes and nodes[0].get("failureSummary"):
            remediation = str(nodes[0]["failureSummary"])[:400]
        if not remediation:
            help_url = str(v.get("helpUrl") or "")
            remediation = (
                f"Resolve this WCAG {impact} accessibility issue. "
                + (f"See: {help_url}" if help_url else "Review WCAG 2.1 AA guidelines for this rule.")
            )
        title = help_text
        if len(title) > 85:
            title = title[:82] + "..."
        out.append(
            ScanFinding(
                category="ada",
                severity=severity,
                title=f"[axe] {title}",
                description=description + (f" ({len(nodes)} element(s) affected)" if nodes else ""),
                remediation=remediation,
                evidence=WebsiteEvidence(
                    page_url=page_url,
                    screenshot_path=shot_map.get(page_url),
                    snippet=snippet,
                    metadata={"axe_rule": rule_id, "impact": impact, "affected_count": len(nodes)},
                ),
                confidence=0.92 if impact in {"critical", "serious"} else 0.85,
            )
        )
    return out


def _maybe_playwright_screenshots(
    urls: list[str], out_dir: Path
) -> tuple[dict[str, str], dict[str, int], list[dict]]:
    """Returns (shot_map, browser_load_ms_by_url, axe_violations).

    Collects real-browser page load timings and axe-core ADA violations alongside
    screenshots. Falls back gracefully if Playwright or the axe CDN is unavailable.
    """
    shots: dict[str, str] = {}
    browser_load_ms: dict[str, int] = {}
    axe_violations: list[dict] = []
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception:
        return shots, browser_load_ms, axe_violations

    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception:
                browser = p.chromium.launch(channel="chrome", headless=True)

            # Desktop screenshot + real-browser performance timing
            page = browser.new_page(viewport={"width": 1366, "height": 768})
            for i, url in enumerate(urls[:3], start=1):
                path = out_dir / f"desktop_{i}.png"
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    page.screenshot(path=str(path), full_page=False)
                    shots[url] = str(path)
                    try:
                        ms = page.evaluate(
                            "() => { const t = performance.timing; "
                            "const end = t.loadEventEnd > 0 ? t.loadEventEnd : t.domContentLoadedEventEnd; "
                            "return (end > 0 && t.navigationStart > 0) ? (end - t.navigationStart) : 0; }"
                        )
                        if isinstance(ms, (int, float)) and int(ms) > 0:
                            browser_load_ms[url] = int(ms)
                    except Exception:
                        pass
                except Exception:
                    continue

            # Axe-core WCAG scan on the first URL (same browser session for efficiency)
            _AXE_CDN = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.9.1/axe.min.js"
            if urls:
                try:
                    axe_page = browser.new_page(viewport={"width": 1366, "height": 768})
                    axe_page.goto(urls[0], wait_until="domcontentloaded", timeout=20000)
                    try:
                        axe_page.add_script_tag(url=_AXE_CDN)
                        axe_page.wait_for_function("typeof axe !== 'undefined'", timeout=8000)
                        raw = axe_page.evaluate(
                            "async () => { try { "
                            "const r = await axe.run({runOnly:{type:'tag',values:['wcag2a','wcag2aa','best-practice']}}); "
                            "return r.violations.slice(0, 20); "
                            "} catch(e) { return []; } }"
                        )
                        if isinstance(raw, list):
                            axe_violations = raw
                    except Exception:
                        pass
                    axe_page.close()
                except Exception:
                    pass

            # Mobile screenshot for first URL
            if urls:
                mobile_page = browser.new_page(viewport={"width": 390, "height": 844})
                mobile_path = out_dir / "mobile_1.png"
                try:
                    mobile_page.goto(urls[0], wait_until="domcontentloaded", timeout=15000)
                    mobile_page.screenshot(path=str(mobile_path), full_page=False)
                    shots[f"{urls[0]}__mobile"] = str(mobile_path)
                except Exception:
                    pass
                mobile_page.close()

            browser.close()
    except Exception:
        return shots, browser_load_ms, axe_violations
    return shots, browser_load_ms, axe_violations


def _fetch_pages(base_url: str) -> tuple[dict[str, str], dict[str, float]]:
    """Returns (pages dict, load_times dict in seconds)."""
    pages: dict[str, str] = {}
    load_times: dict[str, float] = {}
    with httpx.Client(timeout=18.0, follow_redirects=True) as client:
        t0 = time.monotonic()
        root = client.get(base_url)
        root.raise_for_status()
        load_times[str(root.url)] = time.monotonic() - t0
        root_url = str(root.url)
        pages[root_url] = root.text or ""
        links = ABS_LINK_RE.findall(root.text or "")
        candidates = [root_url]
        for link in links:
            candidate = urljoin(root_url, link)
            parsed = urlparse(candidate)
            if parsed.scheme not in {"http", "https"}:
                continue
            if parsed.netloc != urlparse(root_url).netloc:
                continue
            p = parsed.path.lower()
            if p in {"/", ""} or p.startswith(_INNER_PAGE_PREFIXES):
                candidates.append(candidate)
        seen: set[str] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            try:
                t0 = time.monotonic()
                resp = client.get(candidate)
                elapsed = time.monotonic() - t0
                if resp.status_code < 400:
                    pages[str(resp.url)] = resp.text or ""
                    load_times[str(resp.url)] = elapsed
            except Exception:
                continue
    return pages, load_times


def _check_http_redirect(base_url: str) -> bool:
    """Returns True if http:// version redirects to https://."""
    http_url = base_url.replace("https://", "http://", 1)
    try:
        with httpx.Client(timeout=8.0, follow_redirects=False) as client:
            resp = client.get(http_url)
            location = resp.headers.get("location", "")
            return resp.status_code in {301, 302, 307, 308} and location.startswith("https://")
    except Exception:
        return False


def _tls_info(host: str) -> dict[str, Any]:
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=8) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert() or {}
                cipher = ssock.cipher()
        return {
            "ok": True,
            "issuer": str(cert.get("issuer")),
            "not_after": str(cert.get("notAfter")),
            "cipher": str(cipher[0]) if cipher else "",
            "protocol": str(cipher[1]) if cipher and len(cipher) > 1 else "",
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _email_dns(domain: str) -> dict[str, Any]:
    try:
        import dns.resolver  # type: ignore
    except Exception:
        return {"spf": "unknown", "dmarc": "unknown", "dkim": "unknown", "reason": "dnspython_unavailable"}

    def _new_resolver(nameservers: list[str] | None = None):
        r = dns.resolver.Resolver()
        r.lifetime = 5.0
        r.timeout = 3.0
        if nameservers:
            r.nameservers = nameservers
        return r

    resolvers = [_new_resolver(), _new_resolver(["1.1.1.1", "8.8.8.8"])]

    def txt(name: str) -> tuple[list[str], str]:
        # status: ok|nx|noanswer|unknown
        last_err = ""
        saw_noanswer = False
        saw_nx = False
        for resolver in resolvers:
            try:
                ans = resolver.resolve(name, "TXT", lifetime=5.0)
                out: list[str] = []
                for r in ans:
                    try:
                        joined = b"".join(r.strings).decode("utf-8", errors="ignore")
                    except Exception:
                        joined = str(r)
                    out.append(joined)
                return out, "ok"
            except dns.resolver.NXDOMAIN:
                saw_nx = True
            except dns.resolver.NoAnswer:
                saw_noanswer = True
            except Exception as exc:
                last_err = str(exc)
                continue
        if saw_nx:
            return [], "nx"
        if saw_noanswer:
            return [], "noanswer"
        return [], f"unknown:{last_err or 'resolver_error'}"

    root_txt, root_status = txt(domain)
    dmarc_txt, dmarc_status = txt(f"_dmarc.{domain}")
    spf = next((x for x in root_txt if "v=spf1" in x.lower()), "")
    dmarc = next((x for x in dmarc_txt if "v=dmarc1" in x.lower()), "")

    # Try multiple DKIM selectors — many domains use non-default selectors (google, k1, mail, etc.)
    dkim = ""
    dkim_selector = ""
    dkim_status = "unknown"
    for _sel in _DKIM_SELECTORS:
        _cands, _status = txt(f"{_sel}._domainkey.{domain}")
        _found = next((x for x in _cands if "v=dkim1" in x.lower()), "")
        if _found:
            dkim = _found
            dkim_selector = _sel
            dkim_status = "present"
            break
        if _status in {"nx", "noanswer"}:
            dkim_status = "unknown"
        elif str(_status).startswith("unknown:"):
            dkim_status = "unknown"

    # Check DMARC policy strength
    dmarc_policy = "none"
    if dmarc:
        pm = re.search(r"p=(\w+)", dmarc, re.IGNORECASE)
        dmarc_policy = pm.group(1).lower() if pm else "none"

    def _presence_for_spf() -> str:
        if spf:
            return "present"
        if root_status in {"ok", "nx", "noanswer"}:
            return "missing"
        return "unknown"

    def _presence_for_dmarc() -> str:
        if dmarc:
            return "present"
        if dmarc_status in {"ok", "nx", "noanswer"}:
            return "missing"
        return "unknown"

    return {
        "spf": _presence_for_spf(),
        "dmarc": _presence_for_dmarc(),
        "dkim": "present" if dkim else dkim_status,
        "dkim_selector": dkim_selector,
        "dmarc_policy": dmarc_policy,
        "resolver_status": {
            "root": root_status,
            "dmarc": dmarc_status,
            "dkim": dkim_status,
        },
        "records": {"spf": spf[:250], "dmarc": dmarc[:250], "dkim": dkim[:250]},
    }


def _check_robots_txt(base_url: str) -> dict[str, Any]:
    """Fetch and parse robots.txt for indexing and sitemap clues."""
    result: dict[str, Any] = {"found": False, "disallow_all": False, "has_sitemap": False, "raw": ""}
    try:
        parsed = urlparse(base_url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        with httpx.Client(timeout=8.0, follow_redirects=True) as client:
            resp = client.get(robots_url)
            if resp.status_code == 200 and "text" in resp.headers.get("content-type", "text"):
                result["found"] = True
                raw = resp.text[:5000]
                result["raw"] = raw
                # Check for disallow all
                if re.search(r"Disallow:\s*/\s*$", raw, re.MULTILINE):
                    result["disallow_all"] = True
                # Check for sitemap declaration
                if re.search(r"Sitemap:\s*https?://", raw, re.IGNORECASE):
                    result["has_sitemap"] = True
    except Exception:
        pass
    return result


def _check_exposed_files(base_url: str) -> list[dict[str, Any]]:
    """Probe for commonly exposed sensitive paths.

    Returns entries like {"path": "/.env", "status_code": 200}. We only treat
    HTTP 200 as confirmed exposure. HTTP 401/403 is considered access-controlled.
    """
    probe_paths = [
        "/.env",
        "/.git/HEAD",
        "/wp-admin/",
        "/phpmyadmin/",
        "/phpinfo.php",
        "/backup.zip",
        "/wp-config.php.bak",
        "/config.php",
        "/.htaccess",
        "/admin/",
        "/server-status",
    ]
    hits: list[dict[str, Any]] = []
    with httpx.Client(timeout=6.0, follow_redirects=False) as client:
        for path in probe_paths:
            try:
                url = base_url.rstrip("/") + path
                resp = client.get(url)
                if resp.status_code in {200, 401, 403}:
                    hits.append(
                        {
                            "path": path,
                            "status_code": int(resp.status_code),
                        }
                    )
            except Exception:
                continue
    return hits


def _ssl_cert_expiry_days(tls: dict[str, Any]) -> int | None:
    """Parse days until SSL cert expiry from tls_info result. Returns None if unparseable."""
    from datetime import datetime as _dt
    not_after = str(tls.get("not_after") or "").strip("'\" ")
    if not not_after or not_after == "None":
        return None
    # ssl module returns strings like "Sep 25 12:00:00 2025 GMT" or "Sep  5 12:00:00 2025 GMT"
    for fmt in ("%b %d %H:%M:%S %Y %Z", "%b  %d %H:%M:%S %Y %Z"):
        try:
            exp = _dt.strptime(not_after, fmt)
            return (exp - _dt.utcnow()).days
        except ValueError:
            continue
    return None


def _detect_cms(html_text: str) -> dict[str, str]:
    """Detect CMS/platform from HTML meta generator tag or common fingerprints."""
    gen = GENERATOR_RE.search(html_text)
    if gen:
        content = gen.group(1).strip()
        lc = content.lower()
        if "wordpress" in lc:
            return {"cms": "WordPress", "version": content}
        if "wix" in lc:
            return {"cms": "Wix", "version": content}
        if "squarespace" in lc:
            return {"cms": "Squarespace", "version": content}
        if "joomla" in lc:
            return {"cms": "Joomla", "version": content}
        if "drupal" in lc:
            return {"cms": "Drupal", "version": content}
        return {}  # generic/unknown generator tag — not worth a finding
    # WordPress fingerprint without generator meta tag
    if "wp-content" in html_text and "wp-includes" in html_text:
        return {"cms": "WordPress", "version": "version-not-exposed"}
    return {}


def _check_generic_h1(pg_html: str) -> str | None:
    """Return H1 text if it is generic/weak (single H1 that matches a generic pattern or is <10 chars), else None."""
    if len(H1_RE.findall(pg_html)) == 1:
        h1_match = H1_CONTENT_RE.search(pg_html)
        if h1_match:
            h1_text = _clean_text(h1_match.group(1), max_len=100).strip()
            if GENERIC_H1_RE.match(h1_text) or len(h1_text) < 10:
                return h1_text
    return None


def _check_heading_hierarchy(pg_html: str) -> dict[str, int] | None:
    """Return heading counts if page has H1+H3 but no H2 (skipped level), else None."""
    h1 = len(H1_RE.findall(pg_html))
    h2 = len(H2_RE.findall(pg_html))
    h3 = len(H3_RE.findall(pg_html))
    if h3 > 0 and h2 == 0 and h1 > 0:
        return {"h1": h1, "h2": h2, "h3": h3}
    return None


def _check_homepage_thin_content(pg_html: str) -> int | None:
    """Return word count if homepage content is thin (< 300 words), else None."""
    words = WORD_CONTENT_RE.findall(re.sub(r"<[^>]+>", " ", pg_html))
    wc = len(words)
    return wc if wc < 300 else None


def _check_form_field_friction(pg_html: str) -> int | None:
    """Return input count if a form has ≥6 input fields (conversion friction), else None."""
    if not FORM_RE.search(pg_html):
        return None
    count = len(INPUT_TYPE_RE.findall(pg_html))
    return count if count >= 6 else None


def _check_copyright_staleness(html: str) -> int | None:
    """Return the most-recent copyright year if stale (>1 year behind current year), else None."""
    from datetime import datetime as _dt
    footer_area = (html or "")[-5000:]
    years: list[int] = []
    for span in COPYRIGHT_YEAR_RE.findall(footer_area):
        for m in re.findall(r"20(\d{2})", str(span)):
            try:
                years.append(int("20" + m))
            except (ValueError, TypeError):
                pass
    if not years:
        return None
    most_recent = max(years)
    current_year = _dt.utcnow().year
    return most_recent if most_recent < current_year - 1 else None


def _check_form_https_action(pg_html: str, page_url: str) -> ScanFinding | None:
    """Flag forms that submit to HTTP (insecure) endpoints — data transmitted in plaintext."""
    match = FORM_ACTION_HTTP_RE.search(pg_html)
    if not match:
        return None
    insecure_snippet = match.group(0)[:150]
    return ScanFinding(
        category="security",
        severity="high",
        title="Lead form submits to insecure HTTP endpoint",
        description=(
            "A form on this page has an action attribute pointing to an HTTP (unencrypted) endpoint. "
            "Form submissions — including name, email, phone, and inquiry details — are transmitted in "
            "plaintext and can be intercepted by any network observer (e.g., on public Wi-Fi or corporate proxies). "
            "This is a direct privacy risk for every visitor who submits a contact or lead form."
        ),
        remediation=(
            "Update the form action URL from http:// to https://. "
            "If using a third-party form service, confirm their endpoint supports HTTPS and use the secure URL. "
            "Test the form submission after the change to verify data is still delivered correctly. "
            "Also audit any AJAX form handlers for insecure endpoints."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=insecure_snippet,
            metadata={"insecure_form_action_detected": True},
        ),
        confidence=0.95,
    )


def _check_schema_completeness(pg_html: str, page_url: str) -> ScanFinding | None:
    """When LocalBusiness JSON-LD schema is present, check for required contact fields.

    Only fires when a LocalBusiness (or relevant subtype) schema block is found AND
    is missing at least one of the high-value fields: telephone, address, name.
    """
    if not LOCAL_BUSINESS_SCHEMA_RE.search(pg_html):
        return None
    _LOCAL_BUSINESS_SUBTYPES = {
        "localbusiness", "restaurant", "professionalservice", "store",
        "medicalorganization", "financialservice", "realestateagent",
        "plumber", "electrician", "generalcontractor", "homeandconstructionbusiness",
        "foodestablishment", "automotivebusiness", "lodgingbusiness",
    }
    for block in LD_JSON_BLOCK_RE.finditer(pg_html):
        raw = block.group(1).strip()
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        schema_type = str(data.get("@type") or "").lower()
        if schema_type not in _LOCAL_BUSINESS_SUBTYPES:
            continue
        missing = [f for f in ("telephone", "address", "name") if not data.get(f)]
        if missing:
            return ScanFinding(
                category="seo",
                severity="medium",
                title=f"LocalBusiness schema is incomplete (missing: {', '.join(missing)})",
                description=(
                    f"The LocalBusiness JSON-LD block is missing required field(s): {', '.join(missing)}. "
                    "Incomplete schema reduces eligibility for Google's local rich results — including the "
                    "knowledge panel phone number, address, and hours display in SERPs — which are high-value "
                    "local search features that drive direct calls and directions."
                ),
                remediation=(
                    f"Add the missing field(s) to the JSON-LD block: {', '.join(missing)}. "
                    "The 'address' field should use a PostalAddress subtype with streetAddress, "
                    "addressLocality, addressRegion, and postalCode. "
                    "Validate the schema using Google's Rich Results Test "
                    "(https://search.google.com/test/rich-results) after updating."
                ),
                evidence=WebsiteEvidence(
                    page_url=page_url,
                    snippet=raw[:220],
                    metadata={"schema_type": data.get("@type"), "missing_fields": missing},
                ),
                confidence=0.88,
            )
    return None


def _check_open_redirect_params(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect links with open-redirect-pattern query parameters (v20).

    Flags href attributes containing ?url=, ?redirect=, ?next=, ?return=, ?goto=, or ?dest=
    followed by an absolute http(s) URL — a common vector for phishing and link hijacking.
    Confidence is intentionally low (0.65) since many legitimate OAuth flows use similar patterns;
    the finding is informational and warrants developer review before action.
    """
    match = OPEN_REDIRECT_RE.search(pg_html)
    if not match:
        return None
    snippet = match.group(0)[:120]
    return ScanFinding(
        category="security",
        severity="low",
        title="Potential open-redirect parameter in page links",
        description=(
            "One or more links on this page include query parameters commonly associated with "
            "open redirects (e.g. ?url=, ?redirect=, ?next=, ?return=). If the destination is "
            "not validated server-side, attackers can craft phishing URLs that appear to originate "
            "from your domain, eroding visitor trust and potentially triggering browser/email "
            "security warnings."
        ),
        remediation=(
            "Validate all redirect destination values server-side against an explicit allowlist of "
            "trusted domains. Never redirect to arbitrary URLs supplied via query parameters. "
            "Use a server-side mapping (e.g. ?next=dashboard maps to /dashboard) rather than "
            "accepting raw URLs."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=snippet,
            metadata={"open_redirect_param_detected": True},
        ),
        confidence=0.65,
    )


def _check_schema_review_rating(pg_html: str, page_url: str) -> "ScanFinding | None":
    """When LocalBusiness schema is present, check for missing Review/AggregateRating markup (v20).

    Only fires on the root URL when:
    1. A LocalBusiness JSON-LD block is detected, AND
    2. No Review or AggregateRating schema is found.
    Adding AggregateRating schema enables star-rating rich results in Google Search,
    which typically increases local CTR by surfacing visible quality signals.
    """
    if not LOCAL_BUSINESS_SCHEMA_RE.search(pg_html):
        return None
    if REVIEW_SCHEMA_RE.search(pg_html):
        return None
    return ScanFinding(
        category="seo",
        severity="low",
        title="Missing star-rating schema (AggregateRating not found)",
        description=(
            "This page includes LocalBusiness structured data but no AggregateRating or Review "
            "JSON-LD schema. Google can display star ratings in local search results when "
            "AggregateRating is correctly marked up, increasing SERP click-through rate. "
            "Competitors with star ratings visible in search results will consistently draw more "
            "clicks on the same query."
        ),
        remediation=(
            'Add an AggregateRating block to your LocalBusiness JSON-LD: '
            '"aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.8", '
            '"reviewCount": "47"}. Populate with verified review data from Google Business '
            "Profile or a first-party review collection. Validate with Google's Rich Results "
            "Test at https://search.google.com/test/rich-results before publishing."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            metadata={"local_business_schema_present": True, "aggregate_rating_found": False},
        ),
        confidence=0.72,
    )


def _check_duplicate_meta_descriptions(pages: dict[str, str]) -> "ScanFinding | None":
    """Detect pages sharing an identical meta description across the crawled page set (v20).

    Duplicate meta descriptions dilute individual page relevance signals in search results.
    Google rewrites duplicate snippets, reducing your control over how each page appears in SERPs.
    Only fires when ≥2 crawled pages share the exact same non-empty meta description.
    """
    desc_map: dict[str, list[str]] = {}
    for url, pg_html in pages.items():
        match = META_DESC_RE.search(pg_html)
        if not match:
            continue
        desc_text = match.group(1).strip()
        if len(desc_text) < 20:
            continue
        norm = desc_text.lower()
        desc_map.setdefault(norm, []).append(url)

    duplicated = {norm: urls for norm, urls in desc_map.items() if len(urls) >= 2}
    if not duplicated:
        return None

    # Surface the most-duplicated description first
    worst_desc, worst_urls = max(duplicated.items(), key=lambda kv: len(kv[1]))
    snippet_urls = ", ".join(worst_urls[:2]) + (f" +{len(worst_urls) - 2} more" if len(worst_urls) > 2 else "")
    return ScanFinding(
        category="seo",
        severity="medium",
        title=f"Duplicate meta descriptions across {len(worst_urls)} pages",
        description=(
            f"The same meta description appears on {len(worst_urls)} crawled pages "
            f"({snippet_urls}). "
            "Duplicate descriptions cause Google to rewrite SERP snippets for those pages, "
            "reducing your control over messaging and diluting per-page relevance signals."
        ),
        remediation=(
            "Write a unique 120–160 character meta description for every crawled page that "
            "describes the specific content and includes the primary service keyword plus "
            "a location or differentiator. Use your CMS bulk-edit or an SEO plugin to audit "
            "all descriptions before publishing."
        ),
        evidence=WebsiteEvidence(
            page_url=worst_urls[0],
            snippet=f"Duplicate found on: {snippet_urls}",
            metadata={"duplicate_count": len(worst_urls), "affected_urls": worst_urls[:4]},
        ),
        confidence=0.88,
    )


def _check_deprecated_html_elements(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect deprecated/obsolete HTML elements that signal poor code hygiene (v21).

    Tags like <marquee>, <blink>, <font>, <center>, <strike>, <acronym>, and <basefont>
    were deprecated in HTML4 and removed in HTML5. Their presence signals:
    - Legacy codebase that may not render correctly in modern browsers or assistive technology
    - Reduced developer trust from search engine quality signals
    - Potential rendering and accessibility issues with screen readers
    """
    matches = DEPRECATED_HTML_RE.findall(pg_html)
    if not matches:
        return None
    unique_tags = sorted({m.split(">")[0].strip("<").split()[0].lower() for m in matches})[:5]
    count = len(matches)
    tag_list = ", ".join(f"<{t}>" for t in unique_tags)
    return ScanFinding(
        category="seo",
        severity="low",
        title=f"Deprecated HTML elements detected ({count} instance{'s' if count != 1 else ''})",
        description=(
            f"The following obsolete HTML elements were found on this page: {tag_list}. "
            "These tags were deprecated in HTML4/XHTML and removed from the HTML5 standard. "
            "Their presence can indicate an unmaintained codebase and may cause rendering issues "
            "in modern browsers or assistive technologies used by accessibility auditors."
        ),
        remediation=(
            "Replace deprecated elements with modern equivalents: use CSS for styling instead of "
            "<font> and <center>; use <del> or <s> instead of <strike>; use <abbr> instead of <acronym>. "
            "Remove <marquee> and <blink> entirely — these have no accessible replacement. "
            "A one-time codebase audit (grep for these tags) should take under 1 hour."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=tag_list,
            metadata={"deprecated_tags": unique_tags, "instance_count": count},
        ),
        confidence=0.91,
    )


def _check_positive_tabindex(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect elements with positive tabindex values that disrupt keyboard navigation (v21).

    WCAG 2.1 Success Criterion 2.4.3 (Focus Order) requires that navigation order
    be logical and intuitive. Positive tabindex values (tabindex="1", "2", etc.) override
    the natural DOM order and create a custom focus sequence that is difficult to maintain
    and typically results in an unexpected or broken keyboard tab order for users who
    navigate without a mouse (keyboard users, screen reader users, motor-impaired users).
    """
    matches = POSITIVE_TABINDEX_RE.findall(pg_html)
    if not matches:
        return None
    unique_values = sorted(set(int(v) for v in matches))[:6]
    count = len(matches)
    values_str = ", ".join(str(v) for v in unique_values)
    return ScanFinding(
        category="ada",
        severity="medium",
        title=f"Positive tabindex values disrupt keyboard navigation order ({count} found)",
        description=(
            f"Found {count} element(s) with positive tabindex values ({values_str}). "
            "Positive tabindex overrides the natural DOM focus order, creating a custom "
            "keyboard navigation sequence that is fragile and frequently breaks as the page "
            "changes. This violates WCAG 2.1 SC 2.4.3 (Focus Order) and disorients users "
            "who navigate entirely via keyboard or assistive technology."
        ),
        remediation=(
            "Remove all positive tabindex values. Use tabindex=\"0\" to make an element "
            "focusable in DOM order, or tabindex=\"-1\" to make it focusable only via script. "
            "Restructure the HTML source order to match the logical reading/interaction order — "
            "this is the correct long-term fix. Positive tabindex is a code smell that almost "
            "always indicates structural HTML issues that need to be resolved."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=f"tabindex values found: {values_str}",
            metadata={"positive_tabindex_values": unique_values, "instance_count": count},
        ),
        confidence=0.87,
    )


def _check_excessive_inline_styles(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect heavy use of inline style attributes that hinder CSS caching (v21).

    When a significant portion of visual styling is applied via inline style= attributes
    rather than external or internal stylesheets, browsers cannot cache that styling
    separately from HTML. This increases per-request payload, prevents efficient cache
    reuse across pages, and makes the site harder to maintain and restyle. It also
    often correlates with render-blocking behaviour and poor separation of concerns.
    """
    _INLINE_STYLE_THRESHOLD = 20
    matches = INLINE_STYLE_RE.findall(pg_html)
    count = len(matches)
    if count < _INLINE_STYLE_THRESHOLD:
        return None
    severity = "medium" if count >= 40 else "low"
    return ScanFinding(
        category="performance",
        severity=severity,
        title=f"Excessive inline styles detected ({count} style= attributes)",
        description=(
            f"Found {count} inline style= attributes on this page. "
            "Heavy use of inline styles prevents browser CSS caching, increases raw HTML payload, "
            "and makes render optimisation harder. Pages with predominantly inline styling "
            "cannot benefit from stylesheet-level caching between page loads, increasing "
            "data transfer and time-to-paint on repeat visits."
        ),
        remediation=(
            "Move inline styles to a shared external stylesheet or CSS custom properties. "
            "For WordPress/page-builder sites, disable inline style injection in your theme or "
            "plugin settings. Use a CSS audit tool (Chrome DevTools Coverage tab) to identify "
            "which inline rules are overriding stylesheet rules and consolidate them. "
            "Aim to reduce inline style= attributes to under 5 per page for layout-critical overrides only."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            metadata={"inline_style_count": count},
        ),
        confidence=0.82,
    )


def _check_anchor_text_generic(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect links using non-descriptive generic anchor text (v22).

    Anchor text like "click here", "read more", "here", or "learn more" harms both
    SEO (loses keyword relevance signal in the link) and accessibility (WCAG 2.4.4 Link
    Purpose — screen reader users who navigate by link list hear only "click here" with
    no context). This is one of the most common and easiest-to-fix content issues.
    """
    matches = GENERIC_ANCHOR_RE.findall(pg_html)
    count = len(matches)
    if count < 2:
        return None
    severity = "medium" if count >= 5 else "low"
    return ScanFinding(
        category="seo",
        severity=severity,
        title=f"Generic anchor text detected on {count} link(s)",
        description=(
            f"Found {count} link(s) using non-descriptive anchor text "
            f"such as 'click here', 'read more', or 'here'. "
            "Non-descriptive link text is an SEO missed opportunity (Google uses anchor text "
            "as a keyword relevance signal) and an accessibility failure — WCAG 2.4.4 "
            "requires that link purpose is clear from the link text alone or its context."
        ),
        remediation=(
            "Replace generic link text with descriptive phrases that convey destination and intent. "
            "Instead of 'Click here to view our services', write 'View our HVAC service packages'. "
            "This simultaneously improves keyboard/screen-reader navigation and adds keyword relevance "
            "to internal links — zero development cost if using a CMS or page builder."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            metadata={"generic_anchor_count": count},
        ),
        confidence=0.85,
    )


def _check_external_link_security(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect target='_blank' links without rel='noopener noreferrer' (v22).

    When a page opens an external link in a new tab (target='_blank') without
    rel='noopener noreferrer', the linked page can access and manipulate the
    opener window via window.opener — a well-documented tabnapping attack vector.
    Modern browsers (Chrome 88+) auto-add noopener for cross-origin links, but older
    browsers and same-origin targets remain vulnerable. OWASP recommends always
    including rel='noopener noreferrer' for target='_blank' links.
    """
    blank_links = BLANK_TARGET_RE.findall(pg_html)
    if not blank_links:
        return None
    # Count those that already have noopener
    vulnerable = [tag for tag in blank_links if not NOOPENER_ATTR_RE.search(tag)]
    count = len(vulnerable)
    if count < 2:
        return None
    severity = "medium" if count >= 6 else "low"
    return ScanFinding(
        category="security",
        severity=severity,
        title=f"External links missing noopener protection ({count} link(s))",
        description=(
            f"{count} link(s) on this page open in a new tab (target='_blank') but are missing "
            "rel='noopener noreferrer'. Without this attribute, the destination page can access "
            "window.opener and redirect the original tab — a tabnapping phishing technique. "
            "OWASP explicitly recommends rel='noopener noreferrer' for all cross-origin _blank links."
        ),
        remediation=(
            "Add rel=\"noopener noreferrer\" to every <a target=\"_blank\"> link. "
            "Example: <a href=\"https://partner.com\" target=\"_blank\" rel=\"noopener noreferrer\">Visit partner</a>. "
            "Most page builders (Elementor, Squarespace, Wix) expose a 'noopener' checkbox in link settings. "
            "For WordPress, use the built-in block editor which auto-adds noopener in newer versions."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            metadata={"vulnerable_blank_links": count, "total_blank_links": len(blank_links)},
        ),
        confidence=0.88,
    )


def _check_structured_data_errors(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect malformed JSON-LD blocks that fail to parse (v22).

    Structured data (JSON-LD) that contains syntax errors is silently ignored by
    Google's indexing pipeline — businesses lose rich SERP features (star ratings,
    FAQs, events, business hours) without knowing why. Google's Rich Results Test
    rejects malformed JSON-LD. These errors are common after CMS template edits or
    plugin conflicts that inject broken JSON into the schema block.
    """
    import json as _json

    blocks = LD_JSON_BLOCK_RE.findall(pg_html)
    if not blocks:
        return None
    errors: list[str] = []
    for raw in blocks:
        raw = raw.strip()
        if not raw:
            continue
        try:
            _json.loads(raw)
        except (_json.JSONDecodeError, ValueError) as exc:
            errors.append(str(exc)[:80])
    if not errors:
        return None
    return ScanFinding(
        category="seo",
        severity="medium",
        title=f"Malformed JSON-LD structured data ({len(errors)} block(s) with errors)",
        description=(
            f"Found {len(errors)} JSON-LD block(s) on this page that contain syntax errors and "
            "cannot be parsed. Google silently ignores invalid structured data, which means any "
            "rich SERP features (star ratings, business hours, FAQs, event listings) linked to this "
            "schema markup are not being served. Common causes: unclosed strings, trailing commas, "
            "or conflicting plugin-injected JSON blocks."
        ),
        remediation=(
            "Copy each JSON-LD block into Google's Rich Results Test "
            "(https://search.google.com/test/rich-results) or jsonlint.com to identify the exact "
            "syntax error. Fix the malformed block, then re-validate. For WordPress sites, check "
            "if multiple SEO plugins (Yoast + RankMath) are both injecting schema and conflicting."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=errors[0] if errors else "",
            metadata={"error_count": len(errors), "first_error": errors[0] if errors else ""},
        ),
        confidence=0.93,
    )


def _check_input_autocomplete_missing(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect email/tel form inputs without autocomplete attribute (v22).

    WCAG 2.1 Success Criterion 1.3.5 (Identify Input Purpose, Level AA) requires that
    inputs collecting personal information have appropriate autocomplete attributes.
    Missing autocomplete on email/phone fields creates friction for users with cognitive
    disabilities who rely on browser autofill, and also reduces conversion rate for
    all users — browsers warn about forms without proper autocomplete hints.
    """
    email_tel_inputs = INPUT_AUTOCOMPLETE_FIELD_RE.findall(pg_html)
    if not email_tel_inputs:
        return None
    # Check if the form block overall has any autocomplete attributes
    missing = [tag for tag in email_tel_inputs if not HAS_AUTOCOMPLETE_ATTR_RE.search(tag)]
    count = len(missing)
    if count < 1:
        return None
    return ScanFinding(
        category="ada",
        severity="low",
        title=f"Email/phone inputs missing autocomplete attribute ({count} field(s))",
        description=(
            f"Found {count} email or phone input field(s) without an autocomplete attribute. "
            "WCAG 2.1 SC 1.3.5 (Identify Input Purpose, Level AA) requires autocomplete on inputs "
            "that collect personal contact information. Missing autocomplete attributes prevent browser "
            "autofill — a significant friction point for users with motor disabilities or cognitive "
            "impairments, and also reduces form completion rates for all users."
        ),
        remediation=(
            'Add the appropriate autocomplete attribute to each field: '
            'autocomplete="email" for email fields, autocomplete="tel" for phone fields, '
            'autocomplete="name" for name fields. '
            'Example: <input type="email" name="email" autocomplete="email">. '
            'These are standard HTML attributes supported in all modern browsers with no development overhead.'
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            metadata={"inputs_missing_autocomplete": count},
        ),
        confidence=0.80,
    )


def _check_missing_og_description(pg_html: str, page_url: str) -> "ScanFinding | None":
    """seo/low: og:title present but no og:description — incomplete Open Graph metadata (v23).

    When a page defines an og:title but omits og:description, social share previews
    on Facebook, LinkedIn, iMessage, and Slack render with an empty description pane.
    This reduces click-through from social shares and signals an incomplete meta strategy.
    """
    if not OG_TITLE_RE.search(pg_html):
        return None  # No og:title either — different/broader check handles missing OG entirely
    if OG_DESC_RE.search(pg_html):
        return None  # og:description is already present
    return ScanFinding(
        category="seo",
        severity="low",
        title="Open Graph description missing despite og:title being set",
        description=(
            "This page sets og:title but has no og:description meta tag. Social media platforms "
            "(Facebook, LinkedIn, iMessage, Slack) use og:description to populate the preview card "
            "description. Without it, shares render with a blank description box, reducing click-through "
            "rates on link previews. Complete Open Graph metadata also supports knowledge panel construction."
        ),
        remediation=(
            'Add <meta property="og:description" content="..."> to the page <head>. '
            "Write 100–200 character descriptions that summarize the page value and include a soft CTA "
            "('Book online today' or 'Call us for a free estimate'). "
            "Most CMS platforms (Yoast for WordPress, Squarespace built-in, Wix SEO panel) have a dedicated "
            "Open Graph description field — no developer required."
        ),
        evidence=WebsiteEvidence(page_url=page_url),
        confidence=0.87,
    )


def _check_meta_keywords_legacy(pg_html: str, page_url: str) -> "ScanFinding | None":
    """seo/low: <meta name='keywords'> still present — deprecated since 2009 (v23).

    Google officially stopped using meta keywords as a ranking factor in 2009.
    Maintaining them is wasted effort and can attract scraper bots that mine keywords
    for competitor intelligence.
    """
    if not META_KEYWORDS_RE.search(pg_html):
        return None
    match = META_KEYWORDS_RE.search(pg_html)
    snippet = match.group(0)[:120] if match else None
    return ScanFinding(
        category="seo",
        severity="low",
        title="Legacy meta keywords tag detected (deprecated signal)",
        description=(
            "A <meta name='keywords'> tag was found on this page. Google formally deprecated meta keywords "
            "as a ranking signal in 2009, and Bing followed. Keyword scrapers and competitor tools actively "
            "harvest meta keywords to reverse-engineer targeting strategy. Maintaining them provides zero "
            "SEO benefit while exposing your keyword strategy to competitors."
        ),
        remediation=(
            "Remove the meta keywords tag from all page templates. Modern SEO relies on content quality, "
            "structured data, and page experience signals — not meta keywords. "
            "Redirect the effort to updating meta descriptions and title tags with current keyword targets, "
            "which do affect SERP click-through rates."
        ),
        evidence=WebsiteEvidence(page_url=page_url, snippet=snippet),
        confidence=0.91,
    )


def _check_table_accessibility(pg_html: str, page_url: str) -> "ScanFinding | None":
    """ada/medium: <table> elements without <th> header cells violate WCAG 1.3.1 (v23).

    Screen readers announce header context for each data cell so users understand what
    column/row the data belongs to. Tables without <th scope='col'/'row'> elements
    present data as an undifferentiated stream to assistive technologies.
    """
    if not TABLE_RE.search(pg_html):
        return None  # No tables on this page
    if TH_ELEMENT_RE.search(pg_html):
        return None  # Has <th> elements — headers present
    table_count = len(TABLE_RE.findall(pg_html))
    return ScanFinding(
        category="ada",
        severity="medium",
        title=f"Data table{'s' if table_count > 1 else ''} missing header cells ({table_count} found)",
        description=(
            f"Found {table_count} HTML table(s) without any <th> header elements. "
            "Screen readers (JAWS, NVDA, VoiceOver) announce header context for each cell so that "
            "users navigating by row or column understand what the data means. Tables without <th> "
            "or scope= attributes present data as an undifferentiated list, failing WCAG 2.1 "
            "Success Criterion 1.3.1 (Info and Relationships, Level A)."
        ),
        remediation=(
            "Add <th scope='col'> for column headers and <th scope='row'> for row headers in every data table. "
            "For complex multi-level tables, also add a <caption> element describing the table purpose. "
            "If tables are used purely for visual layout, replace them with CSS flexbox or grid layout — "
            "layout tables require role='presentation' at minimum. "
            "Validate with axe DevTools (free browser extension) or the WAVE tool."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            metadata={"table_count": table_count},
        ),
        confidence=0.84,
    )


def _check_autoplaying_media(pg_html: str, page_url: str) -> "ScanFinding | None":
    """ada/low-medium: video/audio with autoplay but without muted attr violates WCAG 1.4.2 (v23).

    WCAG 2.1 SC 1.4.2 (Audio Control, Level A) requires that audio playing automatically
    for more than 3 seconds can be paused, stopped, or muted. Unmuted autoplaying video/audio
    is also disorienting for users with vestibular disorders and cognitive disabilities.
    """
    autoplay_tags = AUTOPLAY_MEDIA_RE.findall(pg_html)
    if not autoplay_tags:
        return None
    # Muted autoplay (e.g., hero background video) is generally acceptable
    unmuted = [tag for tag in autoplay_tags if not MUTED_ATTR_RE.search(tag)]
    if not unmuted:
        return None
    severity = "medium" if len(unmuted) >= 2 else "low"
    return ScanFinding(
        category="ada",
        severity=severity,
        title=f"Autoplaying media without mute attribute ({len(unmuted)} instance{'s' if len(unmuted) > 1 else ''})",
        description=(
            f"Found {len(unmuted)} video or audio element(s) that autoplay without the 'muted' attribute. "
            "Autoplaying audio can be disorienting or painful for users with vestibular disorders, cognitive "
            "disabilities, and hearing aids. This violates WCAG 2.1 Success Criterion 1.4.2 (Audio Control, "
            "Level A), which requires a mechanism to pause, stop, or mute auto-starting media within 3 seconds."
        ),
        remediation=(
            "For background hero video: add the 'muted' attribute — muted autoplay is acceptable. "
            "For audio content: never autoplay; provide a visible play button instead. "
            "If autoplay is required for business reasons, add visible pause/mute controls in the player UI. "
            'Example for safe hero video: <video autoplay muted loop playsinline src="hero.mp4"></video>. '
            "Remove autoplay entirely from <audio> elements."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=unmuted[0][:120],
            metadata={"unmuted_autoplay_count": len(unmuted)},
        ),
        confidence=0.88,
    )


def _check_focus_outline_suppressed(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect CSS rules that remove focus outlines, breaking keyboard navigation (v24).

    WCAG 2.1 Success Criterion 2.4.7 (Focus Visible, Level AA) requires that any
    keyboard-operable interface component is visible when focused. A common anti-pattern
    is `outline: none` or `outline: 0` in CSS to remove the browser's default focus ring
    for aesthetic reasons — but this leaves keyboard-only users (tab navigators, screen reader
    users, motor-impaired users) with no way to see which element is currently active.

    This is one of the most frequently cited WCAG violations in ADA demand letters because
    it completely blocks keyboard navigation for users who cannot use a mouse.
    """
    style_blocks = STYLE_BLOCK_RE.findall(pg_html)
    if not style_blocks:
        return None
    combined_css = " ".join(style_blocks)
    if not FOCUS_OUTLINE_SUPPRESS_RE.search(combined_css):
        return None
    return ScanFinding(
        category="ada",
        severity="high",
        title="CSS suppresses focus outline (outline: none/0 detected)",
        description=(
            "A CSS rule containing 'outline: none' or 'outline: 0' was found in a <style> block on this page. "
            "This removes the browser's default keyboard focus ring, violating WCAG 2.1 SC 2.4.7 (Focus Visible, Level AA). "
            "Users who navigate by keyboard — including people with motor impairments and screen reader users — "
            "cannot see which element is active, making the site effectively unusable without a mouse. "
            "ADA demand letters frequently cite suppressed focus outlines as a primary violation."
        ),
        remediation=(
            "Remove 'outline: none' and 'outline: 0' from all CSS rules that apply to focusable elements. "
            "Instead, style a custom focus indicator using 'outline: 3px solid #005fcc' or a "
            "box-shadow-based focus ring: 'box-shadow: 0 0 0 3px rgba(0, 95, 204, 0.5)'. "
            "The :focus-visible pseudo-class lets you show focus rings only for keyboard navigation "
            "without affecting mouse click aesthetics: ':focus-visible { outline: 3px solid #005fcc; }'. "
            "Test with Tab key navigation — every interactive element must show a clear visual indicator. "
            "Use axe DevTools or the Chrome Accessibility panel to validate WCAG 2.4.7 compliance."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet="outline: none detected in <style> block",
            metadata={"wcag_criterion": "2.4.7", "level": "AA", "check": "focus_visible"},
        ),
        confidence=0.76,
    )


def _check_form_submit_button(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect forms without a visible submit button (v24).

    A form that lacks a submit button (either <button type="submit"> or
    <input type="submit">) is inaccessible to keyboard-only users and may
    cause submission confusion on mobile. Many CMS themes and page builders
    accidentally omit the explicit type="submit" attribute, leaving a <button>
    with no type — which defaults to "submit" in HTML5 but breaks in some
    legacy form handlers and screen reader announcements.

    Beyond accessibility, missing or ambiguous submit buttons hurt conversion:
    unclear form actions increase abandonment, particularly on lead-gen and
    contact forms where the CTA button copy matters for click-through intent.
    """
    has_form = bool(FORM_RE.search(pg_html))
    if not has_form:
        return None
    has_submit = bool(SUBMIT_ELEMENT_RE.search(pg_html))
    if has_submit:
        return None
    return ScanFinding(
        category="conversion",
        severity="medium",
        title="Form found without explicit submit button",
        description=(
            "A <form> element was detected on this page but no <button type=\"submit\"> or "
            "<input type=\"submit\"> element was found. Forms without an explicit submit element "
            "rely on implicit JavaScript submission, which breaks for keyboard-only users and "
            "may not function correctly in all browsers. Unclear submission pathways also increase "
            "form abandonment — users may not know how to complete the form action."
        ),
        remediation=(
            "Add an explicit submit button to every form: "
            '<button type="submit" class="btn btn-primary">Send Message</button>. '
            "Use clear, action-oriented button copy ('Get My Free Quote', 'Schedule Consultation') "
            "rather than generic 'Submit'. Ensure the button is visually prominent and "
            "positioned immediately after the last input field. "
            "Test form submission with keyboard only (no mouse) to confirm the Tab + Enter flow works."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet="<form> found — no type=submit button detected",
            metadata={"has_form": True, "has_submit_button": False},
        ),
        confidence=0.73,
    )


def _check_html_lang_region(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect HTML lang attribute missing a regional subtag (v24).

    WCAG 2.1 SC 3.1.2 (Language of Parts, Level AA) and good practice recommend
    specifying the language AND region code in the HTML lang attribute to enable
    correct pronunciation in screen readers (e.g., 'en-US' for American English
    vs 'en-GB' for British English). A bare 'en' attribute is technically valid
    but misses regional spelling, pronunciation, and hyphenation cues that assistive
    technologies use to improve speech synthesis quality for regional audiences.

    For SMBs serving a specific US market, 'lang="en-US"' is the correct value and
    also provides a minor SEO signal for geographic targeting.
    """
    m = LANG_ATTR_CAPTURE_RE.search(pg_html)
    if not m:
        return None
    lang_value = m.group(1)
    # Already has a region subtag (e.g., en-US, fr-CA, pt-BR)
    if "-" in lang_value:
        return None
    return ScanFinding(
        category="ada",
        severity="low",
        title=f"HTML lang attribute missing regional subtag (lang=\"{lang_value}\")",
        description=(
            f"The HTML lang attribute is set to '{lang_value}' without a regional subtag. "
            "WCAG 2.1 SC 3.1.2 (Language of Parts) recommends specifying the full BCP 47 "
            f"language tag including region — e.g., '{lang_value}-US' for US-based sites. "
            "Screen readers use the regional code to select the correct speech synthesis voice, "
            "pronunciation dictionary, and hyphenation rules. Without the region code, "
            "assistive technologies default to a generic dialect that may mispronounce "
            "region-specific terms, place names, and proper nouns."
        ),
        remediation=(
            f"Update the HTML opening tag: change lang=\"{lang_value}\" to lang=\"{lang_value}-US\" "
            f"(or the appropriate region code for your primary audience). "
            "Example: <html lang=\"en-US\">. This is a one-line change in your CMS theme template "
            "or HTML head file. In WordPress, update the language setting in Settings → General → "
            "Site Language, which controls the lang attribute automatically."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=f'lang="{lang_value}" (no region code)',
            metadata={"lang_value": lang_value, "wcag_criterion": "3.1.2"},
        ),
        confidence=0.71,
    )


def _check_carousel_autorotation(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect auto-rotating carousels/sliders without adequate pause controls (v24).

    WCAG 2.1 SC 2.2.2 (Pause, Stop, Hide, Level A) requires that any moving,
    blinking, or auto-updating content that starts automatically AND lasts more than
    5 seconds must provide a mechanism to pause, stop, or hide it. Auto-rotating hero
    carousels are one of the most common violations — they cause particular problems for:
    - Users with vestibular disorders (motion sensitivity, dizziness)
    - Users with cognitive disabilities (distraction from moving elements)
    - Screen reader users who lose context when content shifts

    Additionally, carousels with autoplay are a well-documented conversion killer:
    most visitors never interact with slides beyond the first one, and auto-rotation
    hides primary CTAs from users who read at different speeds.
    """
    has_carousel = bool(CAROUSEL_RE.search(pg_html))
    if not has_carousel:
        return None
    # Only flag if there's evidence of autoplay (data-interval or JS autoplay config)
    has_autoplay = bool(CAROUSEL_INTERVAL_RE.search(pg_html))
    if not has_autoplay:
        return None
    has_pause = bool(CAROUSEL_PAUSE_RE.search(pg_html))
    return ScanFinding(
        category="ada",
        severity="medium" if not has_pause else "low",
        title="Auto-rotating carousel detected" + (" without pause control" if not has_pause else " — verify pause control accessibility"),
        description=(
            "An auto-rotating carousel or slider with automatic advancement was detected. "
            "WCAG 2.1 SC 2.2.2 (Pause, Stop, Hide, Level A) requires a mechanism to pause, "
            "stop, or hide auto-updating content. Auto-rotation also causes vestibular disorder "
            "symptoms for motion-sensitive users and significantly reduces content engagement — "
            "research consistently shows most users never advance past the first carousel slide. "
            + ("No pause/stop control was found in the HTML." if not has_pause else
               "A pause indicator was found — verify it is keyboard-accessible and clearly labeled.")
        ),
        remediation=(
            "Add a visible, keyboard-accessible pause button adjacent to the carousel. "
            "Better: disable autoplay entirely. Studies show static hero sections with a single "
            "primary CTA outperform rotating carousels for conversion. If autoplay is required: "
            "set data-pause='hover', provide visible prev/next controls, and ensure pause/play "
            "is reachable via keyboard Tab + Enter. Use the prefers-reduced-motion CSS media "
            "query to disable animation for users with motion sensitivity settings enabled: "
            "@media (prefers-reduced-motion: reduce) { .carousel { animation: none; } }"
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet="carousel/slider with autoplay detected",
            metadata={"has_pause_control": has_pause, "wcag_criterion": "2.2.2", "level": "A"},
        ),
        confidence=0.72,
    )


def _check_canonical_mismatch(pg_html: str, page_url: str, root_url: str) -> "ScanFinding | None":
    """Detect canonical tags pointing to a different URL than the current page (v24).

    A canonical tag that canonicalizes a page to a different URL tells Google to
    treat the current page as a duplicate and attribute all ranking value to the
    canonical URL. This is correct when intentionally deduplicating paginated/filtered
    content, but when misconfigured (e.g., all pages point to the homepage), it
    causes the current page to be effectively deindexed from search results for its
    intended keywords — a severe, silent SEO error that often goes undetected.

    Fires only for inner pages (not root URL), since homepage-to-homepage canonical is correct.
    """
    # Only check inner pages; root URL self-canonical is correct behavior
    if page_url.rstrip("/") == root_url.rstrip("/"):
        return None
    m = CANONICAL_RE.search(pg_html)
    if not m:
        return None
    canonical_href = m.group(1).strip().rstrip("/")
    norm_current = page_url.rstrip("/")
    norm_root = root_url.rstrip("/")
    # Canonical points to root or to something clearly different from current page
    if canonical_href == norm_root or (
        canonical_href and canonical_href != norm_current and not canonical_href.startswith(norm_current)
    ):
        return ScanFinding(
            category="seo",
            severity="medium",
            title=f"Canonical tag points away from this page (canonicalized to: {canonical_href[:80]})",
            description=(
                f"The canonical tag on this inner page ({page_url}) points to "
                f"'{canonical_href}' — a different URL. "
                "This instructs search engines to treat the current page as a duplicate and "
                "pass all ranking signals to the canonical target. If this is not intentional "
                "(e.g., all pages accidentally point to the homepage), these inner pages will "
                "be deindexed and lose all organic ranking ability for their specific keywords. "
                "This is a common misconfiguration in CMS themes and SEO plugins."
            ),
            remediation=(
                "Verify the canonical tag is correct for each page: it should either be a "
                "self-referencing canonical (matching the page's own URL) or an intentional "
                "canonical to a preferred duplicate. To audit: open Chrome DevTools → Elements → "
                "search for 'canonical'. For WordPress sites, check your Yoast or Rank Math "
                "settings — misconfigured canonical overrides in SEO plugins are a common cause. "
                "Fix: set each page's canonical to its own URL using your SEO plugin's page-level "
                "canonical field, or remove the canonical tag if there is no duplicate content issue."
            ),
            evidence=WebsiteEvidence(
                page_url=page_url,
                snippet=f"canonical href=\"{canonical_href[:120]}\"",
                metadata={"canonical_href": canonical_href, "page_url": page_url},
            ),
            confidence=0.86,
        )
    return None


def _check_video_captions_absent(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect <video> elements missing a <track kind='captions'> element (v25).

    WCAG 1.2.2 (Captions — Prerecorded, Level A) requires synchronized captions for
    all prerecorded audio content in synchronized media. Video without captions bars
    deaf and hard-of-hearing users from content entirely, and DOJ ADA Title III
    enforcement actions increasingly cite missing video captions as an actionable
    accessibility barrier. A single absence can be the basis for an ADA demand letter
    from plaintiffs' attorneys who deploy automated scanners.
    """
    video_tags = VIDEO_ELEMENT_RE.findall(pg_html)
    if not video_tags:
        return None
    has_caption_track = bool(TRACK_CAPTION_RE.search(pg_html))
    if has_caption_track:
        return None
    count = len(video_tags)
    return ScanFinding(
        category="ada",
        severity="medium" if count >= 2 else "low",
        title=f"Video content missing captions track ({count} video element{'s' if count > 1 else ''})",
        description=(
            f"{count} <video> element(s) were detected on this page without an associated "
            "<track kind='captions'> or <track kind='subtitles'> element. "
            "WCAG 1.2.2 (Captions — Prerecorded, Level A) requires synchronized captions for "
            "all prerecorded audio in video. Deaf and hard-of-hearing users cannot access the "
            "information conveyed in these videos. DOJ ADA Title III enforcement and serial ADA "
            "plaintiff litigation frequently cite missing video captions as an actionable violation — "
            "a single uncaptioned video on a public-facing website has been sufficient grounds for "
            "demand letters and settlement agreements in documented cases."
        ),
        remediation=(
            "Add <track kind='captions' src='captions.vtt' srclang='en' label='English'> "
            "inside each <video> element. Create WebVTT (.vtt) caption files for each video. "
            "Free auto-captioning: upload to YouTube Studio (Settings → Subtitles → Auto-generate), "
            "review/edit, then export as .vtt. For self-hosted video, Rev.com charges $1.50/min "
            "for human-edited captions. For embedded YouTube/Vimeo, ensure closed captions are "
            "enabled in the platform settings and verify they display in the embed. "
            "Test with: NVDA+Chrome or macOS VoiceOver to verify captions appear."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=video_tags[0][:120],
            metadata={"video_count": count, "has_caption_track": False, "wcag_criterion": "1.2.2", "level": "A"},
        ),
        confidence=0.82,
    )


def _check_autocomplete_off_personal_fields(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect forms/inputs using autocomplete='off' on personal data fields (v25).

    WCAG 1.3.5 (Identify Input Purpose, Level AA) requires that inputs collecting
    personal information support browser autocomplete. Setting autocomplete='off'
    at the form or input level disables this, forcing re-entry every time and
    increasing abandonment rates — especially on mobile, where typing is already
    friction-heavy. This check does NOT flag password fields (where autocomplete='off'
    or 'new-password' is a recognized security practice).
    """
    has_form_off = bool(FORM_AUTOCOMPLETE_OFF_RE.search(pg_html))
    input_off_count = len(INPUT_AUTOCOMPLETE_OFF_RE.findall(pg_html))
    # Only fire if we have meaningful autocomplete-off suppression (not just 1 isolated input)
    if not has_form_off and input_off_count < 2:
        return None
    # Exclude pages that are primarily login/password-reset flows
    if PASSWORD_INPUT_RE.search(pg_html) and input_off_count <= 1:
        return None
    description_parts = []
    if has_form_off:
        description_parts.append("a <form> element with autocomplete='off'")
    if input_off_count >= 2:
        description_parts.append(f"{input_off_count} input fields with autocomplete='off'")
    combined = " and ".join(description_parts)
    return ScanFinding(
        category="ada",
        severity="low",
        title="Autocomplete disabled on personal information form fields",
        description=(
            f"This page contains {combined}. "
            "WCAG 1.3.5 (Identify Input Purpose, Level AA) requires that inputs collecting "
            "personal data — name, email, address, phone — support browser and password-manager "
            "autocomplete. Disabling autocomplete forces users to re-type information they have "
            "already saved, which increases form abandonment rates by 25–40% on mobile. "
            "Users with motor impairments and cognitive disabilities are disproportionately affected, "
            "as they rely on browser-stored data to reduce repetitive input effort."
        ),
        remediation=(
            "Remove autocomplete='off' from form and personal-data input elements. "
            "Replace with the appropriate autocomplete token: autocomplete='name', 'email', "
            "'tel', 'street-address', etc. (see WHATWG autofill spec). "
            "Only use autocomplete='off' for security-sensitive one-time codes (OTP fields) "
            "or for fields whose values should NOT be retained (e.g., temporary session tokens). "
            "For login forms, use autocomplete='current-password'; for registration, use "
            "autocomplete='new-password'. Most CMS form builders (Gravity Forms, WPForms, "
            "HubSpot Forms) expose this as a per-field setting — no developer required."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=f"autocomplete=off found on {'form' if has_form_off else ''}"
                    f"{' and ' if has_form_off and input_off_count else ''}"
                    f"{f'{input_off_count} inputs' if input_off_count else ''}",
            metadata={"form_level": has_form_off, "input_off_count": input_off_count, "wcag_criterion": "1.3.5"},
        ),
        confidence=0.75,
    )


def _check_placeholder_as_label(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect form inputs using placeholder as the only visible label (v25).

    WCAG 1.3.1 (Info and Relationships, Level A) and 2.5.3 (Label in Name, Level A)
    require that form controls have a persistent, programmatically determinable label.
    Placeholder text disappears when users start typing, leaving them unable to recall
    what the field requires mid-entry. Screen readers may not announce placeholder as
    a meaningful label depending on AT implementation. This pattern is extremely
    common in 'minimal' landing-page form designs and accounts for 25–35% of observed
    form abandonment in UX research studies.
    """
    placeholder_inputs = PLACEHOLDER_INPUT_RE.findall(pg_html)
    if not placeholder_inputs:
        return None
    # Extract input IDs from placeholder-bearing inputs
    input_ids_with_placeholder: list[str] = []
    for inp in placeholder_inputs:
        m = INPUT_ID_RE.search(inp)
        if m:
            input_ids_with_placeholder.append(m.group(1))
    # Get all label for= targets
    label_fors = set(LABEL_FOR_ID_RE.findall(pg_html))
    # Count inputs that have IDs but no matching label
    unlabeled = [iid for iid in input_ids_with_placeholder if iid not in label_fors]
    aria_label_count = len(ARIA_LABEL_ATTR_RE.findall(pg_html))
    # Fire when: ≥2 unlabeled-by-id inputs AND aria labels don't cover the gap
    if len(unlabeled) < 2:
        return None
    if aria_label_count >= len(unlabeled):
        return None  # aria-label covers the gap; accessible
    return ScanFinding(
        category="ada",
        severity="medium",
        title=f"Form inputs use placeholder text as the only label ({len(unlabeled)} fields)",
        description=(
            f"{len(unlabeled)} form input field(s) appear to rely on placeholder text as "
            "their only visible label, without a persistent <label for> or aria-label attribute. "
            "Placeholder text disappears the moment a user begins typing, creating a memory burden "
            "that is especially problematic for users with cognitive disabilities, short-term memory "
            "issues, or anyone who tabulates between fields. "
            "This violates WCAG 1.3.1 (Info and Relationships) and 2.5.3 (Label in Name), both Level A — "
            "the most basic accessibility tier. Screen readers from JAWS, NVDA, and VoiceOver handle "
            "placeholder inconsistently, often failing to announce it as a field label at all. "
            "UX research shows this pattern causes 25–35% higher form abandonment on mobile."
        ),
        remediation=(
            "Add a visible <label for='fieldId'>Field Name</label> element above each input. "
            "If a floating-label design is required, the CSS float animation must preserve the "
            "visible label text AT ALL TIMES (label floats up when focused, not placeholder disappearing). "
            "Quick fix without redesign: add aria-label='Field Name' directly to each input — "
            "this resolves the screen reader issue but not the cognitive/visual one for sighted users. "
            "Best practice: <label for='email'>Email Address</label><input id='email' type='email'> — "
            "no developer framework required, works in any CMS. Gravity Forms and WPForms "
            "both support above-input labels with a single toggle in form settings."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=placeholder_inputs[0][:120] if placeholder_inputs else "",
            metadata={"unlabeled_count": len(unlabeled), "aria_label_count": aria_label_count, "wcag_criterion": "1.3.1"},
        ),
        confidence=0.79,
    )


def _check_pdf_links_without_warning(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect links to PDF files that lack a file-type warning in anchor text (v25).

    WCAG 2.4.4 (Link Purpose, Level A) requires that the purpose of each link can be
    determined from the link text alone or from the link text together with its context.
    Links to PDFs that open in a new viewer tab without warning violate the principle
    of predictable navigation — screen reader users, keyboard navigators, and mobile
    users can be unexpectedly launched into a PDF viewer (or forced to download) without
    consent. On mobile, this is particularly disruptive as PDF rendering is inconsistent
    across browsers and often triggers a full-page redirect or download dialog.
    """
    pdf_anchors = PDF_LINK_ANCHOR_RE.findall(pg_html)
    if not pdf_anchors:
        return None
    no_warning: list[str] = []
    for anchor_text in pdf_anchors:
        clean = re.sub(r"<[^>]+>", "", anchor_text).strip()
        if not re.search(r'\bpdf\b|opens?\s+in\s+new|new\s+(window|tab)|\bdownload\b', clean, re.IGNORECASE):
            no_warning.append(clean[:60] if clean else "(no text)")
    if len(no_warning) < 2:
        return None
    return ScanFinding(
        category="ada",
        severity="low",
        title=f"PDF links missing file-type warning in anchor text ({len(no_warning)} links)",
        description=(
            f"{len(no_warning)} link(s) to PDF files do not indicate the file type in their "
            "anchor text. WCAG 2.4.4 (Link Purpose, Level A) requires that link purpose be "
            "determinable from anchor text. Unmarked PDF links surprise users by launching a "
            "PDF viewer, triggering a download dialog, or causing a full-page navigation — "
            "particularly disruptive for mobile users (whose browsers handle PDFs inconsistently) "
            "and screen reader users who may be navigating a list of links. "
            "Examples of non-compliant anchor text found: "
            + ", ".join(f'"{t}"' for t in no_warning[:3])
            + ("..." if len(no_warning) > 3 else ".")
        ),
        remediation=(
            "Append '(PDF)' or '[PDF, file size]' to the visible anchor text of all PDF links: "
            "<a href='menu.pdf'>Dinner Menu (PDF)</a>. "
            "For documents accessible as web pages, consider converting to HTML for better UX. "
            "To indicate size: <a href='report.pdf'>Annual Report 2024 (PDF, 1.2 MB)</a>. "
            "If design constraints prohibit changing anchor text, add an aria-label: "
            "aria-label='Dinner Menu, PDF file'. "
            "Also add rel='noopener' to PDF links that open in a new tab, and consider "
            "adding target='_blank' deliberately so mobile users know to expect a new context."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=str(no_warning[:3]),
            metadata={"pdf_link_count": len(pdf_anchors), "missing_warning_count": len(no_warning), "wcag_criterion": "2.4.4"},
        ),
        confidence=0.72,
    )


def _check_missing_breadcrumb_schema(pg_html: str, page_url: str, root_url: str) -> "ScanFinding | None":
    """Detect breadcrumb navigation without BreadcrumbList JSON-LD structured data (v25).

    Google uses BreadcrumbList schema to display breadcrumb paths in search results,
    improving CTR by 15–25% for inner pages by showing users the site hierarchy in the
    SERP snippet. Breadcrumb rich results also improve page hierarchy understanding in
    Google's Knowledge Graph. When a page visibly implements breadcrumb navigation
    (via aria-label='breadcrumb' or class='breadcrumb') but lacks the corresponding
    BreadcrumbList JSON-LD, Google cannot generate the breadcrumb SERP feature even
    though the visual element is present — a missed organic click-through opportunity.

    Fires only for inner pages (not root URL); breadcrumb schema on the homepage is
    rare and not the primary use case.
    """
    if page_url.rstrip("/") == root_url.rstrip("/"):
        return None
    has_breadcrumb_nav = bool(BREADCRUMB_NAV_RE.search(pg_html))
    if not has_breadcrumb_nav:
        return None
    has_breadcrumb_schema = bool(BREADCRUMB_SCHEMA_RE.search(pg_html))
    if has_breadcrumb_schema:
        return None
    nav_snippet = BREADCRUMB_NAV_RE.search(pg_html)
    return ScanFinding(
        category="seo",
        severity="low",
        title="Breadcrumb navigation missing BreadcrumbList structured data",
        description=(
            "This page has breadcrumb navigation (detected via aria-label='breadcrumb' or "
            "class='breadcrumb') but lacks BreadcrumbList JSON-LD structured data. "
            "Google uses BreadcrumbList schema to display the breadcrumb path in search result "
            "snippets — e.g., 'Example.com › Services › Web Design'. These breadcrumb rich results "
            "improve click-through rates by 15–25% for inner pages by showing users the site "
            "hierarchy before they click. Without the schema, Google may still detect breadcrumbs "
            "from page structure, but the rich result is not guaranteed and breadcrumbs often "
            "fail to appear. For local service businesses with multi-level site structures "
            "(Home › Services › Plumbing › Emergency Repair), this is a direct organic CTR opportunity."
        ),
        remediation=(
            "Add a BreadcrumbList JSON-LD block to this page's <head>:\n"
            '<script type="application/ld+json">{\n'
            '  "@context": "https://schema.org",\n'
            '  "@type": "BreadcrumbList",\n'
            '  "itemListElement": [\n'
            '    {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://example.com"},\n'
            '    {"@type": "ListItem", "position": 2, "name": "Services", "item": "https://example.com/services"}\n'
            '  ]\n'
            "}</script>\n"
            "WordPress: Yoast SEO and Rank Math both auto-generate BreadcrumbList schema from "
            "navigation — enable in their Schema settings. Free generator: "
            "technicalseo.com/tools/schema-markup-generator/. "
            "Validate output at https://search.google.com/test/rich-results."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=nav_snippet.group(0)[:120] if nav_snippet else "breadcrumb nav detected",
            metadata={"has_breadcrumb_nav": True, "has_breadcrumb_schema": False},
        ),
        confidence=0.78,
    )


def _detect_duplicate_page_titles(pages: dict[str, str]) -> list[tuple[str, list[str]]]:
    """Return list of (norm_title, [urls]) for any page title appearing on ≥2 pages."""
    title_map: dict[str, list[str]] = {}
    for url, pg_html in pages.items():
        t = TITLE_RE.search(pg_html)
        if t:
            title_clean = _clean_text(t.group(1), max_len=120)
            if len(title_clean) >= 20:
                norm = title_clean.lower().strip()
                title_map.setdefault(norm, []).append(url)
    return [(norm, urls) for norm, urls in title_map.items() if len(urls) >= 2]


def _has_custom_404(base_url: str) -> bool:
    """Returns True if the site serves a proper custom 404 page (>1000 bytes body)."""
    probe = base_url.rstrip("/") + "/__check404_xyzabc_missing__"
    try:
        with httpx.Client(timeout=7.0, follow_redirects=True) as client:
            resp = client.get(probe)
            return resp.status_code == 404 and len(resp.content) > 1000
    except Exception:
        return False


def _check_broken_internal_links(pages: dict[str, str], base_url: str) -> "ScanFinding | None":
    """Probe internal links not yet crawled and flag 4xx/5xx responses as SEO findings.

    Extracts hrefs from already-fetched pages, filters to internal links not in
    the crawled pages dict, probes up to 8 candidates with a short HEAD/GET
    request, and aggregates all broken URLs into a single finding.
    """
    parsed_base = urlparse(base_url)
    base_netloc = parsed_base.netloc
    already_fetched = set(pages.keys())

    candidates: set[str] = set()
    for html in pages.values():
        for m in ABS_LINK_RE.finditer(html[:60000]):
            href = m.group(1).strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
                continue
            if href.startswith("/"):
                full_url = f"{parsed_base.scheme}://{base_netloc}{href}"
            elif href.startswith(("http://", "https://")):
                if urlparse(href).netloc != base_netloc:
                    continue
                full_url = href
            else:
                continue
            # Strip query/fragment for clean link probing
            clean = full_url.split("?")[0].split("#")[0].rstrip("/")
            if clean not in already_fetched:
                candidates.add(clean)

    if not candidates:
        return None

    to_probe = sorted(candidates)[:8]
    broken: list[tuple[str, int]] = []
    try:
        with httpx.Client(timeout=5.0, follow_redirects=True) as client:
            for link_url in to_probe:
                try:
                    resp = client.head(link_url)
                    if resp.status_code == 405:
                        resp = client.get(link_url)
                    if resp.status_code >= 400:
                        broken.append((link_url, resp.status_code))
                except Exception:
                    pass
    except Exception:
        pass

    if not broken:
        return None

    severity = "high" if len(broken) >= 3 else "medium"
    url_list = "; ".join(f"{u} (HTTP {s})" for u, s in broken[:5])
    extra = f" (+{len(broken) - 5} more)" if len(broken) > 5 else ""
    return ScanFinding(
        category="seo",
        severity=severity,
        title=f"Broken internal link{'s' if len(broken) > 1 else ''} detected ({len(broken)} found)",
        description=(
            f"Found {len(broken)} internal link(s) returning HTTP error codes: {url_list[:300]}{extra}. "
            "Broken internal links harm user experience and signal site quality issues to search engines — "
            "Google deprioritises sites with crawl errors, and users who hit broken pages typically bounce immediately."
        ),
        remediation=(
            "Fix or remove each broken link. If a page was permanently moved, add a 301 redirect. "
            "Use Google Search Console's Coverage report or a crawler like Screaming Frog to audit "
            "all internal links sitewide and catch additional broken paths."
        ),
        evidence=WebsiteEvidence(
            page_url=base_url,
            snippet=url_list[:220],
            metadata={"broken_count": len(broken), "broken_urls": [u for u, _ in broken[:5]]},
        ),
        confidence=0.94,
    )


def _check_render_blocking_scripts(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect render-blocking script tags in <head> — scripts without async or defer.

    Only fires when 2+ external scripts in <head> are blocking, to avoid false positives
    on minimal pages. Blocking scripts delay first paint and Largest Contentful Paint (LCP).
    """
    head_match = HEAD_SECTION_RE.search(pg_html)
    if not head_match:
        return None
    head_html = head_match.group(1)
    blocking = RENDER_BLOCKING_SCRIPT_RE.findall(head_html)
    count = len(blocking)
    if count < 2:
        return None
    severity = "high" if count >= 5 else "medium"
    snippet = blocking[0][:150] if blocking else ""
    return ScanFinding(
        category="performance",
        severity=severity,
        title=f"Render-blocking scripts detected in <head> ({count} found)",
        description=(
            f"Found {count} external script tag(s) in the document <head> without async or defer attributes. "
            "Render-blocking scripts pause HTML parsing until each script fully loads and executes, "
            "directly delaying first paint and Largest Contentful Paint (LCP) — a Core Web Vitals ranking signal. "
            "Each blocking script can add 200–800ms to perceived load time on mobile connections."
        ),
        remediation=(
            "Add the 'defer' attribute to scripts that manipulate the DOM after load: <script defer src='...'></script>. "
            "Add 'async' to independent scripts (analytics, third-party widgets) that don't depend on page DOM. "
            "Move non-critical scripts to the end of <body> as a fallback. "
            "Use Chrome DevTools Performance panel or PageSpeed Insights to confirm LCP improvement after changes."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=snippet,
            metadata={"blocking_script_count": count},
        ),
        confidence=0.86,
    )


def _check_aria_landmarks(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Check for absence of <main> element or role='main' landmark — a structural ADA gap.

    ARIA landmark roles allow screen reader users to jump directly to main content via
    keyboard shortcuts, bypassing repeated navigation. Missing main landmark is a common
    basis for ADA demand letters targeting structural accessibility deficits.
    """
    if ARIA_MAIN_RE.search(pg_html):
        return None
    return ScanFinding(
        category="ada",
        severity="medium",
        title="No ARIA main landmark detected",
        description=(
            "The page does not include a <main> element or role=\"main\" attribute. "
            "ARIA landmark roles allow screen reader and keyboard users to jump directly to the main "
            "content area — bypassing repeated header and navigation menus. "
            "Missing landmark structure is a WCAG 2.4.1 (Bypass Blocks) Level A failure pattern "
            "and is frequently cited in ADA demand letters as a structural accessibility deficit."
        ),
        remediation=(
            "Wrap the primary page content in a <main> element: <main id=\"main-content\">...</main>. "
            "Pair this with a skip-navigation link targeting #main-content. "
            "Also ensure the site navigation uses <nav> and the page header uses <header> for "
            "complete ARIA landmark coverage. Validate with axe DevTools or NVDA screen reader testing."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            metadata={"aria_main_present": False},
        ),
        confidence=0.82,
    )


def _check_image_dimensions(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Check if images lack explicit width/height attributes — causes Cumulative Layout Shift (CLS).

    When dimensions are unspecified, the browser cannot reserve layout space before images load,
    causing visible content jumps (CLS) — a Core Web Vitals metric that affects search ranking.
    Only fires when 3+ images are missing dimensions to avoid noise on sparse pages.
    """
    all_imgs = IMG_TAG_RE.findall(pg_html)
    if len(all_imgs) < 3:
        return None
    no_dims = IMG_MISSING_DIMS_RE.findall(pg_html)
    count = len(no_dims)
    if count < 3:
        return None
    return ScanFinding(
        category="performance",
        severity="low",
        title=f"Images missing explicit dimensions ({count} of {len(all_imgs)})",
        description=(
            f"Found {count} <img> tags without explicit width and height attributes. "
            "When image dimensions are absent, the browser cannot reserve layout space before images load, "
            "causing Cumulative Layout Shift (CLS) — a Core Web Vitals metric directly tied to page ranking. "
            "Visible content jumps during load create a poor experience, particularly on slower mobile connections."
        ),
        remediation=(
            "Add explicit width and height attributes matching each image's intrinsic pixel dimensions: "
            '<img src="..." width="800" height="450" alt="...">. '
            "In CSS, add img { height: auto; } to maintain aspect ratio while allowing responsive scaling. "
            "This lets the browser allocate space before download, eliminating layout shift. "
            "Use Chrome DevTools Lighthouse to measure CLS before and after the change."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            metadata={"images_total": len(all_imgs), "images_missing_dims": count},
        ),
        confidence=0.78,
    )


def _check_multiple_h1s(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect pages with 2+ H1 tags — dilutes keyword relevance and confuses crawlers.

    Google uses the H1 as the primary topic signal for a page. Multiple H1 tags create
    ambiguity about the page's main subject, undermining targeted keyword authority.
    Only fires when 2+ H1 opening tags are found to avoid false positives.
    """
    h1s = H1_OPEN_RE.findall(pg_html)
    if len(h1s) < 2:
        return None
    count = len(h1s)
    first_h1_match = H1_CONTENT_RE.search(pg_html)
    snippet = first_h1_match.group(0)[:120] if first_h1_match else ""
    return ScanFinding(
        category="seo",
        severity="medium",
        title=f"Multiple H1 tags detected ({count} found)",
        description=(
            f"Found {count} H1 heading tags on this page. "
            "Search engines use the H1 as the primary signal for the page's topic. "
            "Multiple H1s create ambiguity about the main subject, diluting keyword relevance "
            "and confusing crawlers during indexing. "
            "This is a common cause of pages failing to rank for their target keyword despite "
            "on-page optimisation effort elsewhere on the site."
        ),
        remediation=(
            "Consolidate to a single H1 that matches the page's primary target keyword. "
            "Convert all other H1 elements to H2 or H3 to establish a clear heading hierarchy. "
            "Check page templates and widget code — duplicate H1s frequently originate from "
            "injected navigation components or sidebar titles that render as H1 in the DOM."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=snippet,
            metadata={"h1_count": count},
        ),
        confidence=0.88,
    )


def _check_social_proof_absence(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Check homepage for absence of social proof signals (reviews, testimonials, ratings).

    Social proof is one of the highest-leverage conversion signals for SMBs. First-time
    visitors who see no reviews or testimonials face trust friction that reduces contact rate.
    Only fires on homepage to avoid noise from inner pages that legitimately lack reviews.
    """
    if TESTIMONIAL_RE.search(pg_html):
        return None
    if re.search(r'[★⭐]|\b(rating|review|trustpilot|google review|yelp)\b', pg_html, re.IGNORECASE):
        return None
    return ScanFinding(
        category="conversion",
        severity="medium",
        title="No social proof or testimonials detected on homepage",
        description=(
            "No customer testimonials, star ratings, or review references were found on the homepage. "
            "Social proof is one of the most powerful conversion signals for SMBs — visitors who see "
            "positive reviews are significantly more likely to contact or purchase. "
            "Absence of visible social proof creates trust friction, especially for first-time visitors "
            "who have no prior relationship with the business."
        ),
        remediation=(
            "Add 2–3 specific customer testimonials with names and context above the fold or near CTAs. "
            "If you have Google, Yelp, or Trustpilot reviews, embed a review widget or badge. "
            "Include a star rating count (e.g., '4.8 stars from 47 Google Reviews') for immediate credibility. "
            "A dedicated testimonials section placed just before the primary CTA maximises conversion impact."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            metadata={"testimonial_detected": False},
        ),
        confidence=0.80,
    )


def _check_preconnect_hints(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Check if Google Fonts is loaded without a preconnect/dns-prefetch resource hint.

    Without preconnect, the browser cannot start the CDN connection until the Google Fonts
    stylesheet reference is parsed — adding one full round-trip (DNS + TCP + TLS) of latency
    before any font bytes are downloaded. On mobile, this delay is typically 100–300ms.
    """
    if not GOOGLE_FONTS_RE.search(pg_html):
        return None
    if PRECONNECT_RE.search(pg_html):
        return None
    snippet_match = GOOGLE_FONTS_RE.search(pg_html)
    snippet = snippet_match.group(0)[:80] if snippet_match else ""
    return ScanFinding(
        category="performance",
        severity="low",
        title="Google Fonts loaded without preconnect resource hint",
        description=(
            "Google Fonts (fonts.googleapis.com / fonts.gstatic.com) are referenced without a "
            "<link rel='preconnect'> hint in the document head. "
            "Without preconnect the browser must complete DNS lookup, TCP connect, and TLS handshake "
            "before downloading any font bytes — adding 100–300ms of render-blocking latency. "
            "On mobile connections this delay is typically 2–4× higher and directly impacts "
            "First Contentful Paint (FCP), a Core Web Vitals ranking signal."
        ),
        remediation=(
            "Add these two preconnect hints in <head> before the Google Fonts stylesheet link: "
            "<link rel='preconnect' href='https://fonts.googleapis.com'> "
            "<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>. "
            "This allows the browser to start the CDN connection in parallel with other resource loading. "
            "For maximum savings, consider self-hosting fonts via the google-webfonts-helper tool "
            "to eliminate the external CDN round-trip entirely."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=snippet,
            metadata={"preconnect_hint_present": False},
        ),
        confidence=0.84,
    )


def _check_jquery_outdated(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect use of outdated jQuery versions (1.x or 2.x) — known security vulnerabilities.

    jQuery 1.x reached end-of-life in January 2019 and has multiple known CVEs including XSS
    vulnerabilities (CVE-2019-11358, CVE-2020-11022/11023). jQuery 2.x is also EOL.
    Only fires when a version string with major < 3 is detected in the page HTML.
    """
    match = JQUERY_VERSION_RE.search(pg_html)
    if not match:
        return None
    try:
        major = int(match.group(1))
        minor = int(match.group(2))
    except (IndexError, ValueError):
        return None
    if major >= 3:
        return None  # jQuery 3.x is current
    version_str = f"{major}.{minor}"
    # Very old jQuery 1.7 or older = high severity; remaining 1.x and all 2.x = medium
    is_very_old = major == 1 and minor <= 7
    severity = "high" if is_very_old else "medium"
    snippet = match.group(0)[:80]
    return ScanFinding(
        category="security",
        severity=severity,
        title=f"Outdated jQuery version detected ({version_str}.x)",
        description=(
            f"jQuery v{version_str} was detected in the page source. "
            "jQuery 1.x and 2.x are past end-of-life and contain publicly documented vulnerabilities "
            "including XSS injection risks (CVE-2019-11358, CVE-2020-11022/11023). "
            "Outdated JavaScript libraries are one of the most common attack vectors used by automated "
            "bots to exploit SMB websites, and are flagged as a security risk signal."
        ),
        remediation=(
            f"Upgrade to jQuery 3.7 or later. The migration guide at jquery.com/upgrade-guide/3.0/ "
            "covers breaking changes — most sites require only minor syntax adjustments. "
            "If your theme or plugin still loads the old version, contact the vendor or replace with "
            "a maintained alternative. Validate with Chrome DevTools > Sources after updating."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=snippet,
            metadata={"jquery_major": major, "jquery_minor": minor},
        ),
        confidence=0.87,
    )


def _check_third_party_scripts(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect excessive third-party script loading — causes performance and privacy risk.

    Each external script domain adds a DNS lookup + TCP connection overhead.
    5+ third-party script domains is a significant performance bottleneck and increases attack
    surface. Only fires when 5+ distinct external domains are found to avoid false positives.
    """
    matches = EXTERNAL_SCRIPT_SRC_RE.findall(pg_html)
    domains: set[str] = set()
    for host in matches:
        host = host.lower().strip()
        if host:
            domains.add(host)
    count = len(domains)
    if count < 5:
        return None
    domain_list = sorted(domains)
    snippet = ", ".join(domain_list[:5])
    return ScanFinding(
        category="performance",
        severity="medium" if count >= 8 else "low",
        title=f"High third-party script load ({count} external domains)",
        description=(
            f"Found {count} distinct external script domains loading on this page. "
            "Each external domain requires a separate DNS lookup, TCP connection, and potentially "
            "TLS handshake before script bytes are downloaded — adding 50–300ms of latency per domain. "
            "Third-party scripts also create privacy exposure and can block page rendering "
            "if any external service is slow or unavailable."
        ),
        remediation=(
            "Audit each third-party script for actual business value. "
            "Remove unused tracking pixels, duplicate analytics, or abandoned A/B testing tools. "
            "Consolidate tag management using a single Google Tag Manager container. "
            "Load non-critical scripts with async/defer and consider self-hosting frequently used libraries."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=snippet,
            metadata={"third_party_domain_count": count, "domains_sample": domain_list[:8]},
        ),
        confidence=0.82,
    )


def _check_iframes_without_title(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Check for iframes missing title attributes — an ADA accessibility gap.

    WCAG 2.1 Success Criterion 4.1.2 requires all interface components, including iframes,
    to have accessible names. Screen readers announce untitled iframes as 'frame' with no
    description, making embedded maps, videos, and forms inaccessible to blind users.
    Only fires when at least one iframe lacks a title.
    """
    all_iframes = IFRAME_RE.findall(pg_html)
    if not all_iframes:
        return None
    titled_iframes = IFRAME_TITLE_RE.findall(pg_html)
    untitled_count = len(all_iframes) - len(titled_iframes)
    if untitled_count <= 0:
        return None
    snippet = all_iframes[0][:120]
    return ScanFinding(
        category="ada",
        severity="medium",
        title=f"Iframe(s) missing title attribute ({untitled_count} of {len(all_iframes)})",
        description=(
            f"Found {untitled_count} iframe element(s) without a title attribute. "
            "WCAG 2.1 Success Criterion 4.1.2 (Name, Role, Value — Level A) requires all UI components "
            "including iframes to have programmatically determinable names. "
            "Screen reader users hear only 'frame' with no description, making embedded content "
            "(maps, videos, contact forms, payment widgets) inaccessible. "
            "ADA demand letters frequently cite untitled iframes as a concrete WCAG violation."
        ),
        remediation=(
            'Add a descriptive title attribute to every iframe: <iframe title="Contact us map" src="...">. '
            "The title should describe the iframe's purpose, not its source domain. "
            "For Google Maps embeds, use title=\"Map showing [Business Name] location\". "
            "For YouTube videos, use the video title. Validate with axe DevTools browser extension."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=snippet,
            metadata={"iframe_count": len(all_iframes), "untitled_count": untitled_count},
        ),
        confidence=0.90,
    )


def _check_server_version_disclosure(response_headers: dict, page_url: str) -> "ScanFinding | None":
    """Detect Server/X-Powered-By HTTP headers that expose specific technology version strings.

    Version disclosure helps attackers fingerprint server software and cross-reference
    known CVEs for the exact version. Apache/2.4.50, PHP/7.2.1, nginx/1.14.0 each map
    to publicly-known vulnerabilities. Removing version banners is a zero-downtime,
    zero-cost hardening step recommended by OWASP and CIS benchmarks.
    """
    disclosures: list[str] = []
    for header_name in ("server", "x-powered-by"):
        val = str(response_headers.get(header_name, "") or "").strip()
        if val and SERVER_DISCLOSURE_RE.search(val):
            disclosures.append(f"{header_name}: {val[:80]}")
    if not disclosures:
        return None
    severity = "medium" if any("x-powered-by" in d.lower() for d in disclosures) else "low"
    return ScanFinding(
        category="security",
        severity=severity,
        title="Server technology version disclosed via HTTP headers",
        description=(
            f"The web server is exposing specific version information in HTTP response headers: "
            f"{'; '.join(disclosures)}. "
            "Attackers use version fingerprinting as the first step toward identifying known CVEs "
            "and targeting exploits for your exact server build. This information is visible to anyone "
            "who sends a single HTTP request — no authentication required."
        ),
        remediation=(
            "Remove version details from HTTP response headers at the server layer. "
            "For Apache: set \"ServerTokens Prod\" and \"ServerSignature Off\" in httpd.conf. "
            "For nginx: add \"server_tokens off;\" inside the http{} block in nginx.conf. "
            "For PHP: set \"expose_php = Off\" in php.ini. "
            "For Node.js/Express: call app.disable('x-powered-by') or add the helmet middleware "
            "(npm install helmet; app.use(helmet()))."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=disclosures[0][:120],
            metadata={"disclosed_headers": disclosures},
        ),
        confidence=0.92,
    )


def _check_sri_missing(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect external scripts loaded without Subresource Integrity (SRI) attributes.

    SRI allows browsers to cryptographically verify that CDN-served resources have not
    been modified. Without SRI, a compromised CDN or man-in-the-middle attack can silently
    inject malicious code into every page visit — a supply-chain attack requiring no changes
    to the site itself. Threshold of 3+ scripts avoids FPs on analytics/ad snippets that
    don't support SRI.
    """
    all_cdn_scripts = CDN_SCRIPT_TAG_RE.findall(pg_html)
    missing_sri = [tag for tag in all_cdn_scripts if not INTEGRITY_ATTR_RE.search(tag)]
    if len(missing_sri) < 3:
        return None
    severity = "medium" if len(missing_sri) >= 5 else "low"
    snippet = missing_sri[0][:150] if missing_sri else ""
    return ScanFinding(
        category="security",
        severity=severity,
        title=f"External scripts missing Subresource Integrity ({len(missing_sri)} found)",
        description=(
            f"Found {len(missing_sri)} external JavaScript files loaded without Subresource Integrity "
            "(SRI) integrity= attributes. If the CDN or a third-party host is compromised, attackers "
            "can silently inject malicious JavaScript into your site, harvesting visitor data or "
            "delivering malware — without any changes to your own server. "
            "This is a supply-chain attack vector: visible in multiple high-profile breaches including Magecart."
        ),
        remediation=(
            "Add integrity and crossorigin attributes to external script and link tags. Example: "
            "<script src=\"https://cdn.example.com/lib.js\" "
            "integrity=\"sha384-<hash>\" crossorigin=\"anonymous\"></script>. "
            "Generate SRI hashes at srihash.org or with the CLI: "
            "openssl dgst -sha384 -binary lib.js | openssl base64 -A. "
            "Note: some third-party snippets (ad networks, live chat) intentionally update without "
            "notice — evaluate SRI compatibility per vendor before enforcing."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=snippet[:150],
            metadata={
                "scripts_without_sri": len(missing_sri),
                "total_external_scripts": len(all_cdn_scripts),
            },
        ),
        confidence=0.80,
    )


def _check_compression_enabled(
    base_url: str,
    *,
    response_headers: "dict | None" = None,
    response_size_bytes: "int | None" = None,
) -> "ScanFinding | None":
    """Check whether the web server sends gzip or Brotli compressed HTTP responses.

    When response_headers/response_size_bytes are supplied (from a prior request),
    no additional network call is made — the existing security-headers response is reused.
    Falls back to its own request only when called without pre-fetched data.
    Compression typically reduces HTML/CSS/JS transfer sizes by 60–80%.
    """
    if response_headers is None:
        try:
            with httpx.Client(timeout=8.0, follow_redirects=True) as client:
                r = client.get(base_url)
            response_headers = dict(r.headers)
            response_size_bytes = len(r.content)
        except Exception:
            return None
    content_encoding = str(response_headers.get("content-encoding", "") or "").lower().strip()
    if content_encoding in ("gzip", "br", "deflate", "brotli", "zstd", "compress"):
        return None  # compression is active
    size_kb = (response_size_bytes or 0) // 1024
    if size_kb < 10:
        return None  # page too small to meaningfully benefit from compression
    return ScanFinding(
        category="performance",
        severity="medium",
        title="HTTP response compression not enabled",
        description=(
            f"The server is not using gzip or Brotli compression on HTTP responses "
            f"(uncompressed homepage: ~{size_kb}KB). "
            "Enabling compression typically reduces HTML/CSS/JS transfer sizes by 60–80%, "
            "directly improving Time-to-First-Byte (TTFB) and Largest Contentful Paint (LCP). "
            "Uncompressed responses disproportionately affect mobile visitors — often adding "
            "1–3 seconds on 4G connections."
        ),
        remediation=(
            "Enable compression at the web server or CDN layer. "
            "For nginx: add \"gzip on; gzip_types text/plain text/css application/json "
            "application/javascript text/xml text/javascript;\" to the http{} block in nginx.conf. "
            "For Apache: enable mod_deflate and add "
            "\"AddOutputFilterByType DEFLATE text/html text/css application/javascript\" "
            "to .htaccess or httpd.conf. "
            "For Cloudflare: Speed > Optimization > Content Optimization — enable Brotli."
        ),
        evidence=WebsiteEvidence(
            page_url=base_url,
            metadata={
                "uncompressed_size_kb": size_kb,
                "content_encoding_detected": content_encoding or "none",
            },
        ),
        confidence=0.88,
    )


def _check_noindex_inner_pages(pages: dict, root_url: str) -> "ScanFinding | None":
    """Detect crawled inner pages with noindex meta tags that may be blocking SEO indexing.

    Targets only non-homepage pages — homepage noindex is handled separately as an
    intentional pattern (staging or maintenance). Inner pages with noindex are almost
    always a misconfiguration: a WordPress SEO plugin toggle left on from staging, or the
    'Discourage search engines' setting in WordPress that was never disabled after launch.
    Blocked pages cannot rank for any search query regardless of their content quality.
    """
    noindex_pages: list[str] = []
    for url, html in pages.items():
        if url == root_url:
            continue  # homepage noindex is a separate check
        if NOINDEX_RE.search(html):
            noindex_pages.append(url)
    if not noindex_pages:
        return None
    severity = "high" if len(noindex_pages) >= 3 else "medium"
    affected_str = ", ".join(noindex_pages[:3])
    if len(noindex_pages) > 3:
        affected_str += f" +{len(noindex_pages) - 3} more"
    return ScanFinding(
        category="seo",
        severity=severity,
        title=f"Inner page(s) blocked from indexing via noindex ({len(noindex_pages)} found)",
        description=(
            f"Found {len(noindex_pages)} inner page(s) with a noindex robots meta tag, preventing "
            f"search engines from indexing them: {affected_str}. "
            "This is a common post-launch misconfiguration — often the WordPress 'Discourage search engines' "
            "setting never disabled after staging, or a Yoast/RankMath toggle applied site-wide. "
            "Blocked pages cannot appear in search results regardless of content quality or backlinks."
        ),
        remediation=(
            "Remove the noindex meta tag from pages that should be publicly indexed. Check: "
            "(1) WordPress > Settings > Reading — disable 'Discourage search engines from indexing this site'. "
            "(2) Yoast SEO or RankMath: open each affected page and set 'Allow search engines to show "
            "this page in search results' to enabled. "
            "(3) Review robots.txt for Disallow rules blocking the same paths. "
            "Verify via Google Search Console > Coverage report within 48 hours of changes."
        ),
        evidence=WebsiteEvidence(
            page_url=noindex_pages[0],
            snippet='<meta name="robots" content="noindex">',
            metadata={"noindex_page_count": len(noindex_pages), "sample_urls": noindex_pages[:3]},
        ),
        confidence=0.93,
    )


def _check_csp_weak_directives(response_headers: dict, page_url: str) -> "ScanFinding | None":
    """Detect a Content Security Policy that permits unsafe-inline or unsafe-eval.

    A CSP that contains 'unsafe-inline' AND 'unsafe-eval' in script-src or default-src
    effectively negates the primary XSS mitigation benefit — inline scripts can execute
    freely, defeating the entire purpose of the header. This is worse than a well-crafted
    strict CSP and provides false security assurance. Only fires when CSP IS present but
    is configured with overly permissive directives. Missing CSP is already caught by the
    security headers finding.
    """
    csp = str(response_headers.get("content-security-policy", "") or "").strip()
    if not csp:
        return None  # Missing CSP is handled by the security headers block
    unsafe_matches = CSP_UNSAFE_RE.findall(csp)
    has_unsafe_inline = any("inline" in m.lower() for m in unsafe_matches)
    has_unsafe_eval = any("eval" in m.lower() for m in unsafe_matches)
    if not (has_unsafe_inline and has_unsafe_eval):
        return None
    snippet = csp[:200] if len(csp) > 200 else csp
    return ScanFinding(
        category="security",
        severity="medium",
        title="Content Security Policy allows unsafe-inline and unsafe-eval directives",
        description=(
            "The Content-Security-Policy header is present but configured with both 'unsafe-inline' "
            "and 'unsafe-eval' — two directives that effectively disable the XSS protection a CSP "
            "is designed to provide. Attackers who can inject any HTML (e.g., via a comment field "
            "or form input) can execute arbitrary JavaScript because inline scripts are whitelisted. "
            "This is a common misconfiguration introduced by CMS themes or plugins that require inline "
            "scripts rather than being updated to support nonce-based or hash-based policies."
        ),
        remediation=(
            "Audit which scripts require 'unsafe-inline' and replace with nonce-based or hash-based "
            "allowlisting. For each legitimate inline script, generate a cryptographic hash: "
            "echo -n 'script_content' | openssl dgst -sha256 -binary | openssl base64 -A "
            "and add it to the policy: script-src 'sha256-<hash>'. "
            "Remove 'unsafe-eval' by refactoring code that uses eval(), Function(), or setTimeout "
            "with string arguments. Use a CSP evaluator at csp-evaluator.withgoogle.com to grade "
            "the updated policy before deploying."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=snippet,
            metadata={"csp_has_unsafe_inline": has_unsafe_inline, "csp_has_unsafe_eval": has_unsafe_eval},
        ),
        confidence=0.91,
    )


def _check_cookie_security_flags(response_headers: dict, page_url: str) -> "ScanFinding | None":
    """Check Set-Cookie response headers for missing HttpOnly, Secure, and SameSite flags.

    Session cookies without HttpOnly are readable by JavaScript — any XSS vulnerability
    can harvest them directly. Cookies without Secure may be transmitted over plain HTTP
    connections on mixed networks. Cookies without SameSite=Strict/Lax are exploitable
    in CSRF attacks where a third-party page triggers authenticated requests.
    Only fires when a Set-Cookie header is present AND flags are missing.
    """
    raw = str(response_headers.get("set-cookie", "") or "").strip()
    if not raw:
        return None
    raw_lower = raw.lower()
    missing_flags: list[str] = []
    if "httponly" not in raw_lower:
        missing_flags.append("HttpOnly")
    if "secure" not in raw_lower:
        missing_flags.append("Secure")
    if "samesite" not in raw_lower:
        missing_flags.append("SameSite")
    if not missing_flags:
        return None
    flags_str = ", ".join(missing_flags)
    snippet = raw[:150]
    return ScanFinding(
        category="security",
        severity="medium",
        title=f"Session cookie missing security flags: {flags_str}",
        description=(
            f"A Set-Cookie header was detected that is missing the following security attributes: "
            f"{flags_str}. "
            + (
                "Missing HttpOnly allows JavaScript to access the cookie value — if an XSS vulnerability "
                "exists, attackers can steal session tokens with a single script injection. "
                if "HttpOnly" in missing_flags else ""
            )
            + (
                "Missing Secure means the cookie can be sent over unencrypted HTTP connections on "
                "shared networks (coffee shops, airports), enabling session hijacking. "
                if "Secure" in missing_flags else ""
            )
            + (
                "Missing SameSite exposes the session to cross-site request forgery (CSRF) attacks "
                "where third-party pages silently send authenticated requests using the visitor's session. "
                if "SameSite" in missing_flags else ""
            )
        ),
        remediation=(
            f"Update your server or web framework to set all three cookie security attributes. "
            "In PHP: session_set_cookie_params(['httponly' => true, 'secure' => true, 'samesite' => 'Lax']). "
            "In Node.js/Express: res.cookie('session', value, { httpOnly: true, secure: true, sameSite: 'lax' }). "
            "In nginx: proxy_cookie_path / \"/; HttpOnly; Secure; SameSite=Lax\". "
            "For WordPress: add define('COOKIE_SECURE_AUTH_COOKIE', true) and use a security "
            "plugin (Wordfence, iThemes Security) that enforces secure cookie settings globally."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=snippet,
            metadata={"missing_cookie_flags": missing_flags},
        ),
        confidence=0.86,
    )


def _check_cors_misconfiguration(response_headers: dict, page_url: str) -> "ScanFinding | None":
    """Detect an overly permissive CORS policy (Access-Control-Allow-Origin: *).

    A wildcard CORS policy on a page that serves authenticated content or sets cookies
    allows any third-party website to make cross-origin requests and read the responses.
    Combined with credentials (Allow-Credentials: true), this is a critical CORS
    misconfiguration. Even without credentials, wildcard CORS on API endpoints that
    serve business data is a data-exfiltration risk. Only fires when the wildcard
    is detected — specific origin allowlists are not flagged.
    """
    acao = str(response_headers.get("access-control-allow-origin", "") or "").strip()
    if not acao or not CORS_WILDCARD_RE.match(acao):
        return None
    acac = str(response_headers.get("access-control-allow-credentials", "") or "").lower().strip()
    is_critical = acac == "true"
    severity = "high" if is_critical else "medium"
    return ScanFinding(
        category="security",
        severity=severity,
        title="Permissive CORS policy detected (Access-Control-Allow-Origin: *)",
        description=(
            "The server returns an Access-Control-Allow-Origin: * header, allowing any website "
            "on the internet to make cross-origin requests and read the response. "
            + (
                "CRITICAL: Access-Control-Allow-Credentials is also set to true — this combination "
                "means authenticated requests can be made from any third-party origin, potentially "
                "exposing session data, API responses, and user-specific content to malicious sites. "
                if is_critical else
                "While Allow-Credentials is not set, a wildcard origin policy on pages serving "
                "business data, API responses, or form endpoints creates a data-exfiltration risk — "
                "any external script can silently query and extract information from your server. "
            )
        ),
        remediation=(
            "Replace the wildcard CORS header with an explicit allowlist of trusted origins. "
            "For nginx: add 'add_header Access-Control-Allow-Origin \"https://yourdomain.com\";' "
            "inside your server block (replace with actual trusted origin). "
            "For Apache: use Header always set Access-Control-Allow-Origin \"https://yourdomain.com\" "
            "in .htaccess or httpd.conf. "
            "In Express.js: use cors({ origin: 'https://yourdomain.com' }) instead of cors(). "
            "Audit all endpoints that return ACAO headers — static assets like fonts/images can "
            "safely use * but API and authenticated endpoints should never use a wildcard."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=f"Access-Control-Allow-Origin: {acao}",
            metadata={
                "cors_origin": acao,
                "allow_credentials": acac or "not set",
                "is_critical_combination": is_critical,
            },
        ),
        confidence=0.88,
    )


def _check_next_gen_image_formats(pg_html: str, page_url: str) -> ScanFinding | None:
    """Fire when ≥2 <img> tags reference JPEG/PNG without a <picture> WebP wrapper (v26)."""
    legacy_count = len(LEGACY_IMG_SRC_RE.findall(pg_html))
    if legacy_count < 2:
        return None
    has_picture = bool(PICTURE_ELEMENT_RE.search(pg_html))
    severity = "medium" if legacy_count >= 5 else "low"
    return ScanFinding(
        category="performance",
        severity=severity,
        title=f"Next-gen image formats not used ({legacy_count} legacy JPEG/PNG images)",
        description=(
            f"Found {legacy_count} images served as JPEG or PNG. "
            "WebP images are 25–35% smaller than JPEG at equivalent visual quality, and AVIF is up to 50% smaller. "
            "Switching to modern formats directly improves Largest Contentful Paint (LCP), "
            "a Core Web Vitals metric that Google uses as a direct ranking factor. "
            + ("No <picture> element with WebP source was detected on this page. " if not has_picture else "")
            + "The impact is greatest on mobile visitors where bandwidth and CPU are constrained."
        ),
        remediation=(
            "Serve images in WebP with JPEG fallback using the <picture> element: "
            '<picture><source type="image/webp" srcset="image.webp"><img src="image.jpg" alt="..."></picture>. '
            "For WordPress: install WebP Express or ShortPixel (free tier available) to auto-convert images on upload. "
            "For manual conversion: use cwebp (free CLI — npm install -g cwebp-bin) or squoosh.app (browser-based). "
            "For nginx: configure try_files $uri.webp $uri in location blocks. "
            "Validate LCP improvement with Google PageSpeed Insights after deploying."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            metadata={"legacy_image_count": legacy_count, "picture_element_present": has_picture},
        ),
        confidence=0.72,
    )


def _check_missing_address_element(pg_html: str, page_url: str) -> ScanFinding | None:
    """Fire when a street address is visible but no <address> tag or PostalAddress schema (v26)."""
    has_address_text = bool(ADDRESS_TEXT_RE.search(pg_html))
    if not has_address_text:
        return None
    if ADDRESS_ELEMENT_RE.search(pg_html) or POSTAL_ADDRESS_RE.search(pg_html):
        return None
    match = ADDRESS_TEXT_RE.search(pg_html)
    snippet = _clean_text(match.group(0)) if match else ""
    return ScanFinding(
        category="seo",
        severity="low",
        title="Physical address not marked up with semantic HTML or schema",
        description=(
            "A physical street address was detected on this page but is not wrapped in an <address> HTML element "
            "or structured with PostalAddress JSON-LD schema. "
            "Search engines rely on semantic address markup for local citation consistency and knowledge panel accuracy. "
            "Unmarked addresses are harder for Google to extract and reconcile with Google Business Profile data — "
            "potentially reducing local 3-pack ranking confidence."
        ),
        remediation=(
            "Wrap the business address in the HTML <address> element and add PostalAddress to your LocalBusiness JSON-LD: "
            '"address": {"@type": "PostalAddress", "streetAddress": "123 Main St", "addressLocality": "City", '
            '"addressRegion": "ST", "postalCode": "12345", "addressCountry": "US"}. '
            "Validate using Google's Rich Results Test. "
            "Ensure NAP (Name, Address, Phone) is identical here, in Google Business Profile, and on all citation directories."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=snippet[:120],
            metadata={"address_element_present": False, "postal_schema_present": False},
        ),
        confidence=0.68,
    )


def _check_missing_faq_schema(pg_html: str, page_url: str) -> ScanFinding | None:
    """Fire when FAQ-like HTML is detected without FAQPage JSON-LD structured data (v26)."""
    if not FAQ_CONTENT_RE.search(pg_html):
        return None
    if FAQ_SCHEMA_RE.search(pg_html):
        return None
    return ScanFinding(
        category="seo",
        severity="low",
        title="FAQ content detected without FAQPage schema markup",
        description=(
            "FAQ-like content was detected on this page (accordion elements, FAQ headings, or Q&A patterns) "
            "but no FAQPage JSON-LD structured data was found. "
            "FAQPage schema enables Google to display expandable Q&A entries directly in search results — "
            "these rich snippets increase the visible SERP real estate for the page and can significantly "
            "improve click-through rate without any additional ranking improvement."
        ),
        remediation=(
            'Add a FAQPage JSON-LD block to the page <head>: <script type="application/ld+json">{"@type": "FAQPage", '
            '"mainEntity": [{"@type": "Question", "name": "Your question?", "acceptedAnswer": {"@type": "Answer", '
            '"text": "Your answer here."}}]}</script>. '
            "WordPress users: Yoast SEO Premium and RankMath Pro auto-generate FAQPage schema from Gutenberg FAQ blocks. "
            "Only include genuine, substantive Q&A pairs — do not manufacture questions solely for schema purposes. "
            "Validate with Google's Rich Results Test before publishing."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            metadata={"faq_schema_present": False},
        ),
        confidence=0.70,
    )


def _check_title_separator_inconsistency(pages: dict[str, str]) -> ScanFinding | None:
    """Fire when ≥3 pages have titles and use inconsistent separator characters (v26)."""
    separators_found: set[str] = set()
    page_titles: list[tuple[str, str]] = []
    for url, html in pages.items():
        m = TITLE_SEPARATOR_RE.search(html[:3000])
        if not m:
            continue
        title_text = m.group(1).strip()
        for sep in (" | ", " - ", " — ", " :: ", " > "):
            if sep in title_text:
                separators_found.add(sep)
                page_titles.append((url, title_text))
                break
    if len(separators_found) < 2 or len(page_titles) < 3:
        return None
    example_titles = "; ".join(f"'{t}'" for _, t in page_titles[:3])
    return ScanFinding(
        category="seo",
        severity="low",
        title="Inconsistent title separator style across pages",
        description=(
            f"Pages use {len(separators_found)} different title separator styles "
            f"({', '.join(repr(s.strip()) for s in sorted(separators_found))}). "
            f"Examples: {example_titles[:180]}. "
            "Inconsistent title formatting signals a lack of site-wide SEO governance and may reduce "
            "brand recognition consistency in search result snippets."
        ),
        remediation=(
            "Choose one title separator and apply it consistently: 'Primary Keyword | Brand Name' is the "
            "most widely used SEO-standard format. "
            "For WordPress: configure Yoast SEO's title template under SEO > Search Appearance > General. "
            "For custom sites: update the title generation logic in your CMS template or theme. "
            "Avoid mixing separator characters — Google normalizes some but inconsistency reflects on overall site quality signals."
        ),
        evidence=WebsiteEvidence(
            page_url=page_titles[0][0] if page_titles else "",
            snippet=page_titles[0][1][:100] if page_titles else "",
            metadata={"separator_styles_found": sorted(separators_found), "pages_checked": len(page_titles)},
        ),
        confidence=0.72,
    )


def _check_consent_form_privacy_link(pg_html: str, page_url: str) -> ScanFinding | None:
    """Fire when a contact form collects personal data but no privacy policy link is visible (v26)."""
    if not FORM_RE.search(pg_html):
        return None
    # Only fire on pages where the form has email/text inputs (contact/lead-gen forms)
    if not INPUT_TYPE_RE.search(pg_html):
        return None
    if PRIVACY_POLICY_LINK_RE.search(pg_html):
        return None
    return ScanFinding(
        category="security",
        severity="low",
        title="Contact form missing privacy policy disclosure",
        description=(
            "A contact or lead form collecting personal data (name, email, or phone) was found on this page "
            "without a visible link to a privacy policy. "
            "Under GDPR Article 13 and CCPA, businesses must inform users at the point of data collection "
            "about how their information is stored, processed, and shared. "
            "Missing privacy disclosure at the form level is among the most common violations cited in "
            "regulatory enforcement actions and ADA/CCPA demand letters."
        ),
        remediation=(
            "Add a short privacy note near the form submit button: "
            "'By submitting this form, you agree to our <a href=\"/privacy-policy\">Privacy Policy</a>.' "
            "Ensure your privacy policy explains: what data is collected, how it is used, retention period, "
            "and user rights (deletion, correction, opt-out). "
            "For WordPress with Gravity Forms, WPForms, or Contact Form 7: add a GDPR consent checkbox "
            "with the privacy policy URL in the form settings. "
            "Free compliance check: CookieYes GDPR checker at cookieyes.com/blog/gdpr-compliance-checker/."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            metadata={"has_form": True, "privacy_link_found": False},
        ),
        confidence=0.67,
    )


def _check_viewport_user_scalable(pg_html: str, page_url: str) -> "ScanFinding | None":
    """ADA/medium: viewport meta disables pinch-to-zoom via user-scalable=no or maximum-scale=1 (v27).

    WCAG 2.1 Success Criterion 1.4.4 (Resize Text) requires that text can be resized up to 200%
    without loss of content or functionality. When a viewport meta tag includes user-scalable=no
    or maximum-scale=1, the browser prevents the user from pinching to zoom — directly violating
    this criterion. This is particularly harmful for low-vision users on mobile devices and is
    commonly flagged in DOJ ADA enforcement actions against small businesses.
    """
    if not VIEWPORT_SCALABLE_RE.search(pg_html):
        return None
    # Extract the actual viewport content for the snippet
    meta_match = re.search(
        r'<meta[^>]+name=["\']viewport["\'][^>]*content=["\']([^"\']+)["\']',
        pg_html,
        re.IGNORECASE,
    )
    snippet = meta_match.group(1)[:120] if meta_match else "user-scalable=no detected"
    return ScanFinding(
        category="ada",
        severity="medium",
        title="Viewport meta tag blocks pinch-to-zoom (WCAG 1.4.4 violation)",
        description=(
            "The viewport meta tag on this page disables user-controlled zoom via user-scalable=no "
            "or maximum-scale=1. This violates WCAG 2.1 SC 1.4.4 (Resize Text) and prevents users "
            "with low vision from enlarging content on mobile devices. "
            "Mobile browsers such as Safari and Chrome enforce this restriction literally, "
            "making text and UI elements permanently small for users who rely on zoom to read. "
            "This is one of the most commonly cited accessibility violations in mobile web audits "
            "and ADA demand letters targeting small business websites."
        ),
        remediation=(
            "Update the viewport meta tag to remove user-scalable=no and allow maximum-scale values "
            "greater than 1. The recommended accessible viewport declaration is:\n"
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            "Do not include user-scalable=no or maximum-scale=1. If your site layout breaks when "
            "zoomed, fix the layout rather than restricting the user's ability to zoom. "
            "Test with Chrome DevTools mobile simulation and VoiceOver/TalkBack assistive technology."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=snippet,
            metadata={"wcag_criterion": "1.4.4", "impact": "medium"},
        ),
        confidence=0.91,
    )


def _check_analytics_duplicate_fire(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Performance/low: ≥2 distinct GA4 or Universal Analytics tracking IDs on same page (v27).

    When multiple GA4 measurement IDs (G-XXXXXXXXXX) or Universal Analytics tracking IDs
    (UA-XXXXX-X) appear on the same page, analytics events fire twice per user interaction.
    This inflates session counts, conversion event totals, and pageview metrics by 2x or more —
    producing misleading dashboards that overstate traffic and distort ROI calculations.
    This pattern is common when sites migrate from UA to GA4 but forget to remove the old snippet.
    """
    ids = GA_TRACKING_ID_RE.findall(pg_html)
    # Normalize all IDs to uppercase for deduplication
    unique_ids = list(dict.fromkeys(i.upper() for i in ids))
    if len(unique_ids) < 2:
        return None
    return ScanFinding(
        category="performance",
        severity="low",
        title=f"Duplicate analytics tracking IDs detected ({len(unique_ids)} IDs on same page)",
        description=(
            f"Found {len(unique_ids)} distinct Google Analytics tracking IDs loaded simultaneously "
            f"on this page ({', '.join(unique_ids[:3])}). "
            "When multiple GA4 or Universal Analytics IDs fire simultaneously, every user interaction "
            "is counted 2× or more — inflating session counts, conversion totals, and ad attribution data. "
            "This is a common issue when sites migrate from Universal Analytics (UA-) to GA4 (G-) "
            "without removing the old tracking snippet. Inaccurate analytics produce flawed ROI "
            "calculations and can cause over-bidding in Google Ads campaigns."
        ),
        remediation=(
            "Audit your Tag Manager container or site template to find all places where analytics "
            "tracking code is loaded. Remove all but one active measurement ID per analytics property. "
            "If you are migrating from UA to GA4: (1) Keep only the GA4 G-XXXXXXXX ID, "
            "(2) Remove the legacy analytics.js / ga() snippet entirely, "
            "(3) Use Google Tag Manager to manage a single source-of-truth tag configuration. "
            "After removing duplicates, monitor the Realtime report in GA4 for 48 hours to "
            "confirm session counts drop to expected levels."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=", ".join(unique_ids[:4]),
            metadata={"tracking_ids": unique_ids[:6], "duplicate_count": len(unique_ids)},
        ),
        confidence=0.87,
    )


def _check_missing_meta_description(pg_html: str, page_url: str) -> "ScanFinding | None":
    """SEO/medium: page has no meta description tag (v27).

    The meta description tag is a 120–160 character summary displayed in Google's SERP snippet.
    When absent, Google auto-generates a snippet by extracting text from the page — typically
    producing poor, inconsistent copy that hurts click-through rate. Studies consistently show
    that manually written meta descriptions improve organic CTR by 5–10% for competitive keywords.
    This is one of the most universally recommended on-page SEO improvements.
    """
    if META_DESC_RE.search(pg_html):
        return None
    return ScanFinding(
        category="seo",
        severity="medium",
        title="Missing meta description tag",
        description=(
            "No <meta name='description'> tag was found on this page. "
            "Google uses the meta description to generate the snippet text shown below your page title "
            "in search results. Without it, Google auto-generates a snippet by extracting random text "
            "from the page — which is often irrelevant or poorly formatted. "
            "Well-crafted meta descriptions improve click-through rate (CTR) from organic search "
            "results by making your listing more compelling and relevant to searchers' intent."
        ),
        remediation=(
            "Add a unique meta description to the <head> section of this page:\n"
            '<meta name="description" content="Your 120–160 character description here.">\n'
            "Best practices: (1) Write 120–160 characters — longer descriptions get truncated. "
            "(2) Include the primary keyword naturally. (3) Add a clear value proposition or call-to-action. "
            "(4) Make it specific to this page — never copy the same description across pages. "
            "For WordPress: use Yoast SEO or Rank Math to set meta descriptions per page. "
            "For Squarespace/Wix: use the built-in SEO settings panel for each page."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            metadata={"meta_description_present": False},
        ),
        confidence=0.90,
    )


def _check_image_alt_filename(pg_html: str, page_url: str) -> "ScanFinding | None":
    """SEO/low: ≥2 images with alt text that looks like a filename (v27).

    Alt text that contains file extensions (e.g. alt="logo.png") or is purely numeric/underscore-
    separated (e.g. alt="IMG_1234") provides no descriptive value to search engines or screen reader
    users. Google uses alt text as a contextual signal to understand image relevance — filename-like
    alt text is treated as low-quality content. For screen reader users, hearing "logo dot png" or
    "IMG underscore 1234" is meaningless and confusing.
    """
    matches = ALT_FILENAME_RE.findall(pg_html)
    if len(matches) < 2:
        return None
    examples = [m[:60] for m in matches[:3]]
    return ScanFinding(
        category="seo",
        severity="low",
        title=f"Images using filename-style alt text ({len(matches)} images affected)",
        description=(
            f"Found {len(matches)} image(s) with alt text that contains a file extension or "
            f"numeric string (e.g. '{examples[0]}'). "
            "Alt text that resembles a filename (like 'photo.jpg' or 'IMG_1234') provides no "
            "useful context to Google's image search algorithm or to screen reader users. "
            "Google explicitly recommends writing descriptive, keyword-relevant alt text that "
            "explains what the image shows — this is a direct ranking signal for Google Images "
            "and contributes to the overall page quality score."
        ),
        remediation=(
            "Replace filename-style alt text with concise, descriptive alternatives that explain "
            "what the image shows in context. Examples:\n"
            "- Instead of alt='logo.png' → use alt='Acme Plumbing company logo'\n"
            "- Instead of alt='IMG_0049' → use alt='Licensed plumber repairing kitchen sink'\n"
            "- Instead of alt='services.jpg' → use alt='HVAC technician servicing air conditioner'\n"
            "Keep alt text under 125 characters. For decorative images, use alt='' (empty string) "
            "to indicate they are presentational and should be skipped by screen readers. "
            "In WordPress: update alt text in the Media Library for each image."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet="; ".join(examples),
            metadata={"affected_image_count": len(matches), "example_alts": examples},
        ),
        confidence=0.82,
    )


def _check_form_method_get_sensitive(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Security/medium: form with email/password input uses HTTP GET method (v27).

    HTML forms using method='get' append all form field values directly to the URL as query
    parameters (e.g. /contact?email=user@example.com&message=...). This exposes sensitive data
    in: browser address bar (visible to shoulder-surfers), browser history, server access logs,
    proxy logs, CDN logs, and referrer headers sent to third-party analytics scripts.
    This is especially dangerous when the form collects email addresses, passwords, or personal data.
    """
    # Only fire if both a GET-method form and a sensitive input type are present
    if not FORM_METHOD_GET_RE.search(pg_html):
        return None
    sensitive_input_re = re.compile(
        r'<input[^>]+type=["\'](?:email|password|tel|search)["\'][^>]*>',
        re.IGNORECASE,
    )
    if not sensitive_input_re.search(pg_html):
        return None
    form_snippet_match = FORM_METHOD_GET_RE.search(pg_html)
    snippet = form_snippet_match.group(0)[:120] if form_snippet_match else '<form method="get">'
    return ScanFinding(
        category="security",
        severity="medium",
        title="Contact/lead form uses GET method — exposes user data in URL",
        description=(
            "A form collecting personal data (email, phone, or password) was found using method='get'. "
            "GET forms append all submitted values directly to the URL as query parameters, exposing "
            "them in: the browser address bar (visible to anyone nearby), browser history, server "
            "access logs, CDN/proxy logs, and HTTP Referer headers sent to Google Analytics, "
            "Facebook Pixel, and other tracking scripts. "
            "This violates OWASP's guidance on sensitive data exposure and may constitute a GDPR/CCPA "
            "data handling violation when email or personal contact information is transmitted via URL."
        ),
        remediation=(
            "Change the form method from GET to POST in the HTML:\n"
            '<form method="post" action="/contact">\n'
            "POST submits form data in the HTTP request body rather than the URL, preventing it from "
            "appearing in browser history, server logs, or referrer headers. "
            "Additionally ensure the form submits to an HTTPS endpoint (action='https://...') to "
            "encrypt the data in transit. "
            "For WordPress Contact Form 7 / Gravity Forms / WPForms: the default method is POST — "
            "check if a custom override was applied in the form shortcode or plugin settings."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=snippet,
            metadata={"form_method": "get", "owasp_ref": "A02:2021 Cryptographic Failures / Sensitive Data Exposure"},
        ),
        confidence=0.89,
    )


# ---------------------------------------------------------------------------
# v28 check functions
# ---------------------------------------------------------------------------


def _check_css_animation_reduced_motion(pg_html: str, page_url: str) -> "ScanFinding | None":
    """ADA/medium: CSS @keyframes animations present without prefers-reduced-motion media query (v28).

    WCAG 2.3.3 (Animation from Interactions, Level AAA) and WCAG 2.2.2 (Pause, Stop, Hide, Level A)
    both require that motion animations can be disabled or reduced for users with vestibular
    disorders, motion sensitivity, or attention disorders. The CSS prefers-reduced-motion media
    query is the standard mechanism: @media (prefers-reduced-motion: reduce) { animation: none; }.
    When animations are defined in <style> blocks but the media query is absent, the site fails
    to respect the OS-level "reduce motion" accessibility setting. This affects users who experience
    nausea, dizziness, or cognitive overload from animated UI elements.
    """
    # Only check inline <style> blocks — external stylesheets not accessible without extra requests
    style_blocks = STYLE_BLOCK_RE.findall(pg_html)
    if not style_blocks:
        return None
    combined_styles = "\n".join(style_blocks)
    if not CSS_KEYFRAME_RE.search(combined_styles):
        return None
    # If prefers-reduced-motion already referenced anywhere in the page, no finding
    if REDUCED_MOTION_RE.search(pg_html):
        return None
    keyframe_match = CSS_KEYFRAME_RE.search(combined_styles)
    snippet = keyframe_match.group(0)[:80] if keyframe_match else "@keyframes detected"
    return ScanFinding(
        category="ada",
        severity="medium",
        title="CSS animations lack prefers-reduced-motion accessibility override",
        description=(
            "CSS @keyframes animations were detected in page style blocks without a corresponding "
            "@media (prefers-reduced-motion: reduce) override. Users with vestibular disorders, "
            "motion sensitivity (e.g. BPPV, Meniere's disease), or attention-related conditions "
            "can experience nausea and cognitive disruption from page animations. The macOS and "
            "iOS 'Reduce Motion' system setting communicates this preference to browsers, but "
            "the site's animations will still play without the override. This affects WCAG 2.2.2 "
            "Pause, Stop, Hide and is increasingly cited in ADA demand letters targeting motion-heavy websites."
        ),
        remediation=(
            "Wrap all non-essential animation declarations in a prefers-reduced-motion media query: "
            "@media (prefers-reduced-motion: reduce) { *, *::before, *::after { "
            "animation-duration: 0.01ms !important; animation-iteration-count: 1 !important; "
            "transition-duration: 0.01ms !important; } } "
            "Add this block at the end of each stylesheet. For JavaScript-driven animations "
            "(GSAP, Framer Motion), check window.matchMedia('(prefers-reduced-motion: reduce)').matches "
            "and disable or pause animations accordingly."
        ),
        evidence=WebsiteEvidence(page_url=page_url, snippet=snippet),
        confidence=0.77,
    )


def _check_duplicate_h1_across_pages(pages: dict[str, str]) -> "ScanFinding | None":
    """SEO/medium: same H1 text appears on 2 or more different pages (v28).

    Each page should have a unique H1 that clearly communicates its specific topic to both users
    and search engines. When multiple pages share identical H1 text, Google cannot distinguish
    between them for ranking purposes — each page dilutes the others' topic authority for the
    shared keyword. This is distinct from duplicate <title> tags (which affect SERP snippets)
    and affects the in-page semantic heading structure that Googlebot uses for content categorisation.
    Common causes: CMS templates that fall back to a generic site-name H1, or pages copied from
    a template without updating the primary heading.
    """

    def _extract_h1_text(html: str) -> str:
        m = H1_CONTENT_RE.search(html)
        if not m:
            return ""
        raw = m.group(1)
        # Strip tags and normalise whitespace
        clean = re.sub(r"<[^>]+>", " ", raw)
        return re.sub(r"\s+", " ", clean).strip().lower()

    h1_to_urls: dict[str, list[str]] = {}
    for url, html in pages.items():
        h1_text = _extract_h1_text(html)
        if h1_text and len(h1_text) >= 4:  # skip trivially short H1s
            h1_to_urls.setdefault(h1_text, []).append(url)

    duplicates = [(h1, urls) for h1, urls in h1_to_urls.items() if len(urls) >= 2]
    if not duplicates:
        return None

    # Report the most-duplicated H1
    worst_h1, worst_urls = max(duplicates, key=lambda x: len(x[1]))
    return ScanFinding(
        category="seo",
        severity="medium",
        title=f"Duplicate H1 heading across {len(worst_urls)} pages",
        description=(
            f"The H1 heading '{worst_h1[:60]}' appears on {len(worst_urls)} different pages. "
            "Each page must have a unique primary heading that signals its specific topic to "
            "search engines. Duplicate H1s force Google to arbitrarily choose which page to rank "
            "for the shared keyword, diluting the authority of all affected pages and potentially "
            "causing keyword cannibalisation — where your own pages compete against each other."
        ),
        remediation=(
            "Write a unique H1 for every page that reflects its specific content and target keyword. "
            "Service pages: use 'Your Service in City, State'. About pages: 'Our Story — Business Name'. "
            "Blog posts: the specific article title. "
            "Audit all pages in your CMS template to ensure the H1 is populated from the page's own "
            "title field, not a site-wide fallback. Use Screaming Frog or Google Search Console "
            "Coverage report to spot remaining duplicates."
        ),
        evidence=WebsiteEvidence(
            page_url=worst_urls[0],
            snippet=worst_h1[:100],
            metadata={"duplicate_h1": worst_h1[:80], "affected_pages": len(worst_urls), "sample_urls": worst_urls[:3]},
        ),
        confidence=0.83,
    )


def _check_social_sharing_absent(pg_html: str, page_url: str, root_url: str) -> "ScanFinding | None":
    """Conversion/low: no social sharing buttons on content-rich inner pages (v28).

    Content pages (blog posts, service write-ups, case studies, portfolio items) that describe
    value-adding material are missed organic amplification opportunities when they lack social
    sharing buttons. A visitor who finds value in the content should be prompted to share it —
    even a 1-2% share rate on a page with 500 monthly visitors generates meaningful referral traffic
    and builds domain authority signals. This is especially relevant for local service businesses
    that rely on word-of-mouth: a 'Share on Facebook' button converts digital referrals into the
    social equivalent of a recommendation.
    """
    # Only fire on inner pages with substantial text content (not homepage or nav-heavy pages)
    if page_url == root_url or page_url.rstrip("/") == root_url.rstrip("/"):
        return None
    # Require at least 200 words of content to confirm it's a content page worth sharing
    stripped_words = WORD_CONTENT_RE.findall(re.sub(r"<[^>]+>", " ", pg_html))
    if len(stripped_words) < 200:
        return None
    # Skip pages that already have social sharing widgets
    if SOCIAL_SHARE_WIDGET_RE.search(pg_html):
        return None
    # Only fire on blog/portfolio/case-study type pages
    from urllib.parse import urlparse as _urlparse
    path = _urlparse(page_url).path.lower()
    content_path_signals = ("/blog", "/article", "/post", "/case-study", "/portfolio", "/gallery", "/our-work", "/news")
    if not any(path.startswith(sig) for sig in content_path_signals):
        return None
    return ScanFinding(
        category="conversion",
        severity="low",
        title="No social sharing buttons on content page",
        description=(
            f"This content page ({page_url}) has no social sharing buttons (Facebook, LinkedIn, Twitter/X). "
            "Content-rich pages without sharing CTAs rely entirely on visitors manually copying and "
            "pasting the URL — a friction point that reduces word-of-mouth amplification. For local "
            "service businesses, a single share to a neighbourhood Facebook group can generate "
            "3–10 inbound enquiries from peer-referred prospects who convert at higher rates than "
            "cold traffic."
        ),
        remediation=(
            "Add social sharing buttons at the bottom of content pages using a free widget like "
            "AddToAny (addtoany.com), ShareThis, or simple native share links: "
            "<a href='https://www.facebook.com/sharer/sharer.php?u=PAGE_URL'>Share on Facebook</a>. "
            "For WordPress: install the 'Social Warfare' or 'Monarch' plugin. "
            "Place share buttons immediately after the main content body and before the comments/CTA section."
        ),
        evidence=WebsiteEvidence(page_url=page_url, metadata={"word_count": len(stripped_words)}),
        confidence=0.71,
    )


def _check_external_resource_no_hint(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Performance/low: ≥3 external domains loaded without dns-prefetch/preconnect hints (v28).

    When a browser encounters an external domain (e.g. fonts.googleapis.com, cdn.jsdelivr.net,
    analytics scripts) it must perform a full DNS lookup before the resource can be fetched.
    Each DNS resolution adds 20-120ms of latency (longer on mobile networks). For sites that
    load 5+ external domains — common with fonts, analytics, widget scripts, and CDNs — this
    cumulative DNS resolution time can add 200-600ms to page load. The <link rel='dns-prefetch'>
    and <link rel='preconnect'> resource hints tell the browser to start DNS resolution before
    it encounters the actual resource request, eliminating most of this latency. These hints
    are one-line HTML additions with no performance downside.
    """
    # Extract all unique external domains referenced in <script src> and <link href>
    script_domains = set(EXTERNAL_SCRIPT_SRC_RE.findall(pg_html))
    link_domains = set(EXTERNAL_DOMAIN_HREF_RE.findall(pg_html))
    all_external_domains = script_domains | link_domains
    # Strip port numbers and www for comparison
    all_external_domains = {d.split(":")[0].lstrip("www.") for d in all_external_domains}

    if len(all_external_domains) < 3:
        return None

    # Extract domains that already have resource hints
    hinted_domains = set(RESOURCE_HINT_HREF_RE.findall(pg_html))
    hinted_normalized = {d.split(":")[0].lstrip("www.") for d in hinted_domains}
    # Also check PRECONNECT_RE broadly (catches cases where domains aren't extracted but hints exist)
    preconnect_count = len(hinted_domains)

    unhinted = all_external_domains - hinted_normalized
    if len(unhinted) < 3:
        return None

    sample_domains = sorted(unhinted)[:5]
    return ScanFinding(
        category="performance",
        severity="low",
        title=f"External resources loaded without DNS prefetch hints ({len(unhinted)} domains)",
        description=(
            f"This page loads resources from {len(all_external_domains)} external domains but has "
            f"only {preconnect_count} dns-prefetch or preconnect hints. "
            f"Unoptimised external domains include: {', '.join(sample_domains[:4])}. "
            "Each missing hint means the browser must resolve DNS for that domain after it "
            "encounters the resource request — adding 20-120ms per domain. On mobile connections "
            f"with {len(unhinted)} unoptimised domains, cumulative DNS resolution can contribute "
            "over 400ms to the Time to First Byte (TTFB) and First Contentful Paint (FCP) metrics."
        ),
        remediation=(
            "Add <link> resource hints in the <head> section for each external domain: "
            "<link rel='dns-prefetch' href='//fonts.googleapis.com'> "
            "<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin> "
            "Use dns-prefetch for domains where you only need DNS resolution, "
            "and preconnect (stronger) for critical domains where connections should be established early "
            "(fonts, primary CDN, analytics). Most CMS page builders include a 'site-wide head code' "
            "snippet area where these one-line additions can be inserted without theme modifications."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=f"External domains: {', '.join(sample_domains[:3])}",
            metadata={"external_domain_count": len(all_external_domains), "unhinted_count": len(unhinted), "sample_domains": sample_domains},
        ),
        confidence=0.74,
    )


def _check_robots_blocks_assets(robots_raw: str, base_url: str) -> "ScanFinding | None":
    """SEO/medium: robots.txt disallows CSS/JS/image assets that Googlebot needs to render pages (v28).

    Google's mobile-first indexing requires Googlebot to fully render pages — including CSS styles
    and JavaScript — to assess mobile usability and Core Web Vitals. When robots.txt contains
    Disallow: /css/, Disallow: /js/, or Disallow: /wp-content/, Google cannot access these
    resources and renders the page as unstyled HTML. This causes Google to assess the page as
    mobile-unfriendly (even if the site is responsive) and can suppress mobile search rankings.
    Many older WordPress sites have this misconfiguration in default robots.txt templates from
    pre-2015 CMS guides that recommended blocking wp-content to reduce crawl budget.
    """
    if not robots_raw.strip():
        return None
    matches = ROBOTS_ASSET_DISALLOW_RE.findall(robots_raw)
    if not matches:
        return None
    blocked = [m.strip() for m in matches[:5]]
    return ScanFinding(
        category="seo",
        severity="medium",
        title="robots.txt blocks CSS/JS assets needed for page rendering",
        description=(
            f"The robots.txt file contains Disallow rules blocking asset directories: "
            f"{', '.join(blocked)}. "
            "Google's mobile-first indexing requires Googlebot to download and render CSS and "
            "JavaScript to assess page layout, mobile responsiveness, and Core Web Vitals. "
            "Blocking these directories prevents Google from rendering the page correctly, "
            "causing it to be assessed as unstyled HTML — triggering mobile-unfriendly flags "
            "and suppressing rankings in mobile search results, which now represent the majority "
            "of local service business search traffic."
        ),
        remediation=(
            "Remove the Disallow rules for /css/, /js/, /images/, /assets/, and /wp-content/ "
            "from robots.txt. These paths should be freely accessible to Googlebot for rendering. "
            "In WordPress: go to Yoast SEO > Tools > File editor or use the All in One SEO "
            "robots.txt editor to remove legacy block rules. "
            "After updating, submit the new robots.txt to Google Search Console > Settings > "
            "robots.txt and verify with the Robots Tester tool. "
            "Monitor Google Search Console's Coverage report for mobile usability errors over "
            "the following 2 weeks."
        ),
        evidence=WebsiteEvidence(
            page_url=base_url,
            snippet=f"Disallow: {blocked[0]}" if blocked else "robots.txt asset block detected",
            metadata={"blocked_paths": blocked},
        ),
        confidence=0.88,
    )


def _check_hsts_weak_directives(response_headers: dict, page_url: str) -> "ScanFinding | None":
    """Flag Strict-Transport-Security header that is present but weakly configured (v29).

    Only fires when HSTS is SET but has a max-age < 180 days (15552000 s) or is missing
    the includeSubDomains directive. Missing HSTS entirely is already caught by the
    security-headers block, so this check is complementary, not redundant.
    """
    hsts_value = response_headers.get("strict-transport-security") or ""
    if not hsts_value:
        return None  # missing entirely — caught by sec-headers block
    max_age_match = HSTS_HEADER_RE.search(hsts_value)
    max_age = int(max_age_match.group(1)) if max_age_match else 0
    has_subdomains = bool(HSTS_SUBDOMAIN_RE.search(hsts_value))
    short_max_age = max_age < 15_552_000  # < 180 days
    if not short_max_age and has_subdomains:
        return None  # properly configured
    issues = []
    if short_max_age:
        issues.append(f"max-age={max_age} (< 180 days)")
    if not has_subdomains:
        issues.append("missing includeSubDomains")
    return ScanFinding(
        category="security",
        severity="low",
        title="Strict-Transport-Security header is weakly configured",
        description=(
            f"The HSTS header is present but weakly configured: {'; '.join(issues)}. "
            "A short max-age means browsers will not cache the HTTPS-only policy for long, "
            "allowing temporary downgrade attacks via HTTP. Without includeSubDomains, "
            "subdomains (like mail.yourdomain.com or login.yourdomain.com) remain vulnerable "
            "to protocol downgrade and man-in-the-middle attacks even when the root domain is secured. "
            "OWASP recommends a minimum max-age of 31536000 (1 year) with includeSubDomains."
        ),
        remediation=(
            "Update the Strict-Transport-Security header to: "
            "`Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`. "
            "In nginx: `add_header Strict-Transport-Security \"max-age=31536000; includeSubDomains; preload\" always;` "
            "In Apache .htaccess: `Header always set Strict-Transport-Security \"max-age=31536000; includeSubDomains; preload\"` "
            "Consider submitting your domain to the HSTS Preload List at hstspreload.org after confirming all subdomains support HTTPS."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=hsts_value[:150],
            metadata={"max_age_seconds": max_age, "has_include_subdomains": has_subdomains, "issues": issues},
        ),
        confidence=0.88,
    )


def _check_referrer_policy_unsafe(response_headers: dict, page_url: str) -> "ScanFinding | None":
    """Flag Referrer-Policy header that is explicitly set to an unsafe value (v29).

    Only fires when the header IS present and explicitly permits full URL leakage to
    third-party domains (unsafe-url or no-referrer-when-downgrade). Missing
    Referrer-Policy is already in the security-headers block.
    """
    rp_value = (response_headers.get("referrer-policy") or "").strip().lower()
    if not rp_value:
        return None  # missing — caught by sec-headers block
    if rp_value not in REFERRER_UNSAFE_VALUES:
        return None  # safe value
    return ScanFinding(
        category="security",
        severity="low",
        title="Referrer-Policy header set to unsafe value",
        description=(
            f"The Referrer-Policy header is explicitly set to '{rp_value}', which causes the browser "
            "to send the full URL of every page visited — including query strings with session tokens, "
            "search terms, and customer IDs — to third-party analytics providers, advertising networks, "
            "and CDNs loaded on your site. This leaks private user navigation paths to external services "
            "and is a privacy risk under GDPR and CCPA. The Referrer-Policy was set intentionally (not "
            "missing), so this requires an active config change to fix."
        ),
        remediation=(
            "Change the Referrer-Policy header to a privacy-safe value: "
            "`Referrer-Policy: strict-origin-when-cross-origin` (recommended — sends origin only to cross-origin requests). "
            "In nginx: `add_header Referrer-Policy \"strict-origin-when-cross-origin\" always;` "
            "In Apache: `Header always set Referrer-Policy \"strict-origin-when-cross-origin\"` "
            "Do NOT use 'unsafe-url' or 'no-referrer-when-downgrade' — both expose full URL paths to every third-party resource."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=f"Referrer-Policy: {rp_value}",
            metadata={"unsafe_referrer_policy": rp_value},
        ),
        confidence=0.91,
    )


def _check_soft_404_pages(pages: dict[str, str], root_url: str) -> "ScanFinding | None":
    """Detect inner pages that return HTTP 200 but display 'page not found' content (v29).

    Soft 404s confuse Google: the server says the page exists (200 status) but the content
    says it does not. Google may index these empty pages and dilute crawl budget, or suppress
    them unpredictably. This check examines the body text of crawled inner pages (not the
    root) for 404-like phrases.
    """
    root_norm = root_url.rstrip("/")
    soft_404_urls: list[str] = []
    for url, html in pages.items():
        if not html:
            continue
        if url.rstrip("/") == root_norm:
            continue  # skip homepage
        visible_text = re.sub(r"<[^>]+>", " ", html)[:3000]
        if SOFT_404_TEXT_RE.search(visible_text):
            soft_404_urls.append(url)
    if not soft_404_urls:
        return None
    severity = "medium" if len(soft_404_urls) >= 2 else "low"
    return ScanFinding(
        category="seo",
        severity=severity,
        title=f"Soft 404 pages detected ({len(soft_404_urls)} page{'s' if len(soft_404_urls) > 1 else ''})",
        description=(
            f"{'Multiple pages' if len(soft_404_urls) > 1 else 'A page'} on this site "
            f"({'including: ' + ', '.join(soft_404_urls[:2]) + ((' +' + str(len(soft_404_urls) - 2) + ' more') if len(soft_404_urls) > 2 else '')}) "
            "returned HTTP 200 status but contains 'page not found' or similar error text in the body. "
            "Google calls these 'soft 404s' — the server says the page exists, but the content signals it doesn't. "
            "Google may index these empty pages (wasting crawl budget), suppress them unpredictably from rankings, "
            "or devalue internal links pointing to them. Soft 404s are invisible in a basic HTTP check and require "
            "body-content analysis to detect."
        ),
        remediation=(
            "For each soft 404 URL, configure the web server to return a proper 301 redirect to the correct page "
            "if the URL moved, or return a 404/410 HTTP status if the content no longer exists. "
            "In WordPress/CMS: check for deleted pages or renamed slugs that leave stale URLs returning 200. "
            "Verify with Google Search Console under Coverage > Excluded > 'Soft 404' to find additional affected URLs. "
            "After fixing, submit the sitemap in Search Console to prompt recrawl."
        ),
        evidence=WebsiteEvidence(
            page_url=soft_404_urls[0],
            snippet=soft_404_urls[0],
            metadata={"soft_404_urls": soft_404_urls[:5], "count": len(soft_404_urls)},
        ),
        confidence=0.80,
    )


def _check_missing_website_schema(pg_html: str, page_url: str, root_url: str) -> "ScanFinding | None":
    """Flag missing WebSite JSON-LD schema on the homepage (v29).

    The WebSite schema type enables the Google Sitelinks Searchbox SERP feature — a search
    box that appears directly in Google results for branded queries, letting users search
    the site without visiting it first. It also reinforces brand entity recognition in the
    Knowledge Graph. Only fires on the homepage.
    """
    if page_url.rstrip("/") != root_url.rstrip("/"):
        return None  # only fire on homepage
    if WEBSITE_SCHEMA_RE.search(pg_html):
        return None  # already present
    return ScanFinding(
        category="seo",
        severity="low",
        title="Missing WebSite schema — no sitelinks searchbox eligibility",
        description=(
            "The homepage does not include a WebSite JSON-LD schema block. "
            "Google uses this schema to identify your site as a named brand entity and to enable "
            "the Sitelinks Searchbox feature — a search box that appears directly in Google results "
            "when users search for your business name. Without it, Google must infer your brand identity "
            "without structured data guidance, reducing the chance of enhanced SERP features for branded queries. "
            "WebSite schema is one of the simplest schema additions and is supported by all major CMS platforms."
        ),
        remediation=(
            "Add a WebSite JSON-LD block to the homepage <head>:\n"
            "```json\n"
            '<script type="application/ld+json">\n'
            "{\n"
            '  "@context": "https://schema.org",\n'
            '  "@type": "WebSite",\n'
            '  "name": "Your Business Name",\n'
            '  "url": "https://yourdomain.com",\n'
            '  "potentialAction": {\n'
            '    "@type": "SearchAction",\n'
            '    "target": "https://yourdomain.com/search?q={search_term_string}",\n'
            '    "query-input": "required name=search_term_string"\n'
            "  }\n"
            "}\n"
            "</script>\n"
            "```\n"
            "In WordPress, install Yoast SEO (free) — it adds WebSite schema automatically. "
            "Validate with Google's Rich Results Test at search.google.com/test/rich-results."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet="No WebSite @type detected in JSON-LD blocks",
            metadata={"schema_type_missing": "WebSite", "homepage_only": True},
        ),
        confidence=0.78,
    )


def _check_inline_event_handlers(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Flag pages with excessive inline JavaScript event handlers (v29).

    Inline event handlers (onclick=, onload=, etc.) violate Content Security Policy (CSP)
    best practices and are a recognized maintainability and XSS-risk pattern. When ≥5
    instances are found on a page, it signals that the codebase is not following modern
    separation-of-concerns principles. Pages with many inline handlers cannot use a strict
    CSP without breaking functionality, leaving them more vulnerable to XSS attacks.
    """
    handlers = INLINE_EVENT_HANDLER_RE.findall(pg_html)
    if len(handlers) < 5:
        return None
    severity = "medium" if len(handlers) >= 12 else "low"
    return ScanFinding(
        category="security",
        severity=severity,
        title=f"Excessive inline event handlers detected ({len(handlers)} instances)",
        description=(
            f"This page contains {len(handlers)} inline JavaScript event handlers "
            "(onclick=, onload=, onsubmit=, etc. attributes in HTML). "
            "Inline handlers violate Content Security Policy (CSP) best practices — "
            "a strict CSP (which blocks XSS attacks) requires 'unsafe-inline' to function with inline handlers, "
            "effectively defeating the primary protection benefit. This is an OWASP A03:2021 Injection risk pattern. "
            "Inline handlers are also harder to audit for malicious code injection, since third-party scripts or "
            "CMS plugins that write inline handlers can introduce unreviewed JavaScript execution."
        ),
        remediation=(
            "Move all event handler logic to external JavaScript files and attach events using "
            "`addEventListener()` instead of inline HTML attributes. "
            "For example, replace `<button onclick=\"submitForm()\">` with: "
            "`document.querySelector('#submit-btn').addEventListener('click', submitForm)`. "
            "After removing all inline handlers, implement a strict Content-Security-Policy header "
            "without 'unsafe-inline' to protect against XSS attacks. "
            "Use a JavaScript linter (ESLint) to catch inline handler patterns during development."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=INLINE_EVENT_HANDLER_RE.search(pg_html).group(0)[:80] if INLINE_EVENT_HANDLER_RE.search(pg_html) else "",  # type: ignore[union-attr]
            metadata={"inline_handler_count": len(handlers)},
        ),
        confidence=0.82,
    )


def _check_x_frame_options(response_headers: dict, page_url: str) -> "ScanFinding | None":
    """Flag missing clickjacking protection when CSP is set but frame-ancestors is absent (v30).

    A site that has a Content-Security-Policy header but omits the frame-ancestors directive
    is partially protected by CSP yet still vulnerable to clickjacking. The general missing-
    headers check covers the case where X-Frame-Options is absent without any CSP. This check
    specifically targets the gap where CSP is present (so the general check doesn't flag it
    as missing) but frame-ancestors was forgotten — leaving the page frameable by attackers.
    """
    has_xfo = bool(
        response_headers.get("x-frame-options")
        or response_headers.get("X-Frame-Options")
    )
    csp_value = (
        response_headers.get("content-security-policy")
        or response_headers.get("Content-Security-Policy")
        or ""
    )
    has_csp = bool(csp_value.strip())
    has_frame_ancestors = bool(FRAME_ANCESTORS_CSP_RE.search(csp_value))
    # Only fire when CSP is present (X-Frame-Options was not flagged as missing by general check)
    # but frame-ancestors directive is not included — a specific CSP misconfiguration
    if has_xfo or has_frame_ancestors or not has_csp:
        return None
    return ScanFinding(
        category="security",
        severity="medium",
        title="CSP missing frame-ancestors directive — clickjacking protection incomplete",
        description=(
            "A Content-Security-Policy header is present but does not include a frame-ancestors directive. "
            "Without frame-ancestors 'self', the page can still be embedded in an external iframe, "
            "enabling clickjacking attacks where visitors are tricked into clicking hidden buttons or "
            "entering credentials without realising the page has been framed — an OWASP A05:2021 risk."
        ),
        remediation=(
            "Add frame-ancestors 'self' to the existing Content-Security-Policy header. "
            "Example: Content-Security-Policy: default-src 'self'; frame-ancestors 'self'. "
            "In nginx: add_header Content-Security-Policy \"... frame-ancestors 'self'\"; "
            "In Apache .htaccess: Header always set Content-Security-Policy \"... frame-ancestors 'self'\". "
            "Also consider adding X-Frame-Options: SAMEORIGIN as a fallback for older browsers that "
            "do not yet fully support the frame-ancestors CSP directive."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            metadata={"csp_present": True, "frame_ancestors_present": False, "x_frame_options": False},
        ),
        confidence=0.88,
    )


def _check_select_without_label(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Flag <select> dropdown elements without accessible label associations (v30).

    WCAG 1.3.1 (Info and Relationships) requires that all form controls — including <select>
    dropdowns — have a programmatically determinable label. Without a <label for="..."> or
    aria-label attribute, screen readers announce the dropdown only as 'combo box' with no
    context about what the user should select. This affects elderly and mobility-impaired users
    who use keyboard or switch-access navigation for services like appointment booking or contact forms.
    """
    select_tags = SELECT_ELEMENT_RE.findall(pg_html)
    if not select_tags:
        return None
    # Count selects that carry neither aria-label nor aria-labelledby
    selects_without_aria = [
        s for s in select_tags
        if not re.search(r'\baria-(?:label|labelledby)=["\']', s, re.IGNORECASE)
    ]
    if not selects_without_aria:
        return None
    # Use label[for=] count as a proxy — if fewer labels than selects, some are unlabeled
    label_ids = {m.group(1) for m in SELECT_LABEL_RE.finditer(pg_html)}
    select_ids = {
        m.group(1) for m in re.finditer(r'<select\b[^>]+\bid=["\']([^"\']+)["\']', pg_html, re.IGNORECASE)
    }
    labelled_by_for = len(select_ids & label_ids)
    total_selects = len(select_tags)
    labelled = labelled_by_for + (total_selects - len(selects_without_aria))
    unlabeled = max(0, total_selects - labelled)
    if unlabeled < 1:
        return None
    return ScanFinding(
        category="ada",
        severity="medium",
        title=f"Dropdown menus missing accessible labels ({unlabeled} <select> element(s))",
        description=(
            f"Found {unlabeled} <select> dropdown element(s) without an associated <label for=...> or "
            "aria-label attribute. Screen reader users hear only 'combo box' with no context — they "
            "cannot determine what value to choose (state, service type, date, etc.) without "
            "sighted assistance. This violates WCAG 1.3.1 Info and Relationships (Level A)."
        ),
        remediation=(
            "Associate each <select> with a visible label using the for/id pair: "
            '<label for="state">State</label><select id="state">...</select>. '
            "Where a visible label would clutter the UI, add aria-label=\"Service Type\" directly to "
            "the <select> element. Avoid using the first disabled option ('-- Choose --') as the "
            "sole label — it disappears after selection and is not programmatically associated."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            metadata={"select_count": total_selects, "unlabeled_count": unlabeled},
        ),
        confidence=0.81,
    )


def _check_above_fold_cta(pg_html: str, page_url: str, root_url: str) -> "ScanFinding | None":
    """Flag homepage missing a clear call-to-action in the above-fold content (v30).

    When a visitor lands on the homepage, they scan the first screenful (above the fold)
    in under 5 seconds. If no clear action prompt is visible — 'Book Now', 'Get a Quote',
    'Call Now', 'Contact Us', 'Schedule a Consultation' — visitors with buying intent
    disengage and bounce. This check inspects the first ~1,200 chars of stripped body text,
    which approximates the above-fold viewport content for most small business sites.
    Only fires on the root URL to avoid false positives on inner pages.
    """
    if page_url.rstrip("/") != root_url.rstrip("/"):
        return None
    # Strip HTML tags from the first ~6000 chars of raw HTML to approximate visible text
    stripped = re.sub(r'<[^>]+>', ' ', pg_html[:6000])
    stripped = re.sub(r'\s+', ' ', stripped).strip()
    above_fold_text = stripped[:1200]
    if CTA_RE.search(above_fold_text):
        return None
    return ScanFinding(
        category="conversion",
        severity="medium",
        title="No clear call-to-action visible in homepage above-fold content",
        description=(
            "No action-oriented phrase (such as 'Book Now', 'Get a Quote', 'Call Now', "
            "'Contact Us', or 'Schedule a Free Consultation') was detected in the first screenful "
            "of homepage text. Visitors who don't see an immediate next step within 3–5 seconds "
            "are significantly more likely to bounce — especially mobile visitors arriving from ad "
            "clicks, Google Maps, or 'near me' search results who have high transactional intent."
        ),
        remediation=(
            "Place a prominent, action-specific CTA button in the hero/banner section above the fold. "
            "Use specific, service-tied language: 'Schedule a Free Consultation', 'Request a Quote Today', "
            "or 'Call (555) 000-0000 Now'. Ensure the button is visually distinct (contrasting color, "
            "≥16px bold text) and links directly to a contact form, phone number, or booking tool. "
            "On mobile, the CTA should be tappable (≥44px target height) and visible without scrolling."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=above_fold_text[:200],
        ),
        confidence=0.71,
    )


def _check_unminified_resources(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Flag pages loading ≥3 non-CDN scripts/stylesheets without .min. in filename (v30).

    Unminified JavaScript and CSS files are typically 30–70% larger than their minified
    counterparts, directly increasing page weight and parse time. When multiple unminified
    resources are loaded from the same origin (not a CDN), it signals that the build pipeline
    hasn't been configured for production delivery — a common issue in small-business sites
    maintained by generalist developers or built with outdated CMS themes.
    """
    all_matches = UNMIN_SCRIPT_RE.findall(pg_html)
    unminified = [
        u for u in all_matches
        if ".min." not in u.lower()
        and not re.search(
            r'(?:cdn\.|googleapis\.com|cloudflare\.com|jsdelivr\.net|unpkg\.com|cdnjs\.cloudflare|bootstrapcdn\.com)',
            u, re.IGNORECASE,
        )
    ]
    count = len(unminified)
    if count < 3:
        return None
    severity = "medium" if count >= 5 else "low"
    snippet = ", ".join(u[-50:] for u in unminified[:3])
    return ScanFinding(
        category="performance",
        severity=severity,
        title=f"Unminified JS/CSS resources detected ({count} non-CDN files)",
        description=(
            f"Found {count} script or stylesheet resources loaded without a .min. minified variant "
            "from the same origin (not a CDN). Unminified files are significantly larger than their "
            "minified equivalents, increasing page weight and JavaScript parse time on every visit. "
            "This directly affects Core Web Vitals scores and mobile load performance."
        ),
        remediation=(
            "Enable asset minification in your build pipeline or CMS settings. "
            "In WordPress, use a caching plugin (WP Rocket, LiteSpeed Cache, or W3 Total Cache) to "
            "automatically minify JS and CSS. For custom builds, add a minification step "
            "(UglifyJS/Terser for JS, cssnano for CSS) to your deployment workflow. "
            "Use PageSpeed Insights to measure improvement after enabling minification."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=snippet[:220],
            metadata={"unminified_count": count},
        ),
        confidence=0.70,
    )


def _check_missing_h2_headings(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Flag content-rich pages (≥400 words) with no H2 subheadings — weak content structure (v30).

    H2 headings serve two critical functions: they signal topical section structure to Google
    (improving crawlability and keyword coverage) and they provide navigation landmarks for
    screen readers (WCAG 2.4.6 Headings and Labels). A page with substantial word count but
    no H2s is a wall of text that frustrates both search engines and keyboard-navigating users.
    Only fires when the page has meaningful content (≥400 words) to avoid false positives on
    thin pages where H2s are genuinely not needed.
    """
    h2_count = len(H2_RE.findall(pg_html))
    if h2_count > 0:
        return None
    word_count = len(WORD_CONTENT_RE.findall(pg_html))
    if word_count < 400:
        return None
    return ScanFinding(
        category="seo",
        severity="low",
        title=f"Content-rich page has no H2 subheadings ({word_count} words)",
        description=(
            f"This page contains approximately {word_count} words but has no H2 heading elements. "
            "H2 subheadings help Google understand the topical sections of a page and improve keyword "
            "coverage for long-tail search queries. They also provide visual and navigational structure "
            "for readers, reducing bounce rate on longer content pages. "
            "WCAG 2.4.6 (Level AA) recommends using headings to identify sections of content."
        ),
        remediation=(
            "Break the page content into logical sections and add descriptive H2 headings for each. "
            "Example: if this is a services page, use H2 headings for each service area "
            "(e.g., 'Our Plumbing Services', 'Emergency Repair', 'Installation & Replacement'). "
            "Each H2 should naturally incorporate secondary keywords relevant to that section. "
            "Target 2–5 H2s per page for well-structured content pages, using H3 for sub-points."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            metadata={"word_count": word_count, "h2_count": 0},
        ),
        confidence=0.77,
    )


def _check_cache_control_headers(response_headers: dict, page_url: str) -> "ScanFinding | None":
    """Flag responses missing Cache-Control headers or set to no-store (v31).

    Without Cache-Control, browsers must re-download the full page HTML on every visit because
    they have no instruction on how long to keep a cached copy. Setting Cache-Control: no-store
    is even more aggressive — it prohibits browsers and CDNs from keeping any copy, forcing a
    full server round-trip on every page load. For public marketing pages this is almost always
    a misconfiguration: it increases server load, adds 200–400ms latency for repeat visitors on
    slow connections, and prevents Cloudflare/CDN edge caching from working. Integrated into the
    security-headers try block to reuse the existing httpx response (no extra network request).
    """
    # Normalize header key lookup (header dict keys may be mixed case)
    cc = ""
    for k, v in response_headers.items():
        if k.lower() == "cache-control":
            cc = str(v or "").strip().lower()
            break
    if not cc:
        return ScanFinding(
            category="performance",
            severity="medium",
            title="Missing Cache-Control header on page response",
            description=(
                "The server is not sending a Cache-Control header with HTML page responses. "
                "Without explicit caching instructions, browsers and CDNs cannot cache the page, "
                "forcing a full server round-trip on every visit — even for repeat visitors. "
                "This increases server load, page latency for mobile users, and bandwidth costs. "
                "Google's PageSpeed Insights and Lighthouse flag missing cache headers as a performance issue. "
                "Repeat visitors on high-latency connections (4G mobile) experience the full page load time "
                "on every visit with no caching benefit."
            ),
            remediation=(
                "Add Cache-Control: max-age=3600, must-revalidate to HTML responses and "
                "Cache-Control: max-age=31536000, immutable to versioned static assets (CSS, JS, images). "
                "In nginx: add_header Cache-Control 'max-age=3600, must-revalidate'; "
                "In Apache .htaccess: Header set Cache-Control 'max-age=3600, must-revalidate'. "
                "Cloudflare's free tier (cloudflare.com) caches assets at edge nodes automatically."
            ),
            evidence=WebsiteEvidence(
                page_url=page_url,
                metadata={"cache_control": "absent"},
            ),
            confidence=0.80,
        )
    if "no-store" in cc:
        return ScanFinding(
            category="performance",
            severity="low",
            title="Cache-Control: no-store prevents all browser caching on public page",
            description=(
                "The server sends Cache-Control: no-store, instructing browsers and CDNs to never "
                "cache the page or retain any cached copy. While appropriate for sensitive authenticated "
                "pages (banking portals, medical dashboards), this is typically a misconfiguration for "
                "public marketing pages. Every page load requires a full server round-trip, adding "
                "200–400ms of latency for repeat visitors and preventing CDN edge caching from reducing "
                "origin server load."
            ),
            remediation=(
                "For public pages, replace Cache-Control: no-store with Cache-Control: max-age=3600, must-revalidate. "
                "Reserve no-store only for pages containing authenticated session data or sensitive user-specific content. "
                "Verify and correct this setting in your CMS cache plugin (W3 Total Cache, WP Rocket), "
                "reverse proxy config (nginx/Apache), or CDN edge rules."
            ),
            evidence=WebsiteEvidence(
                page_url=page_url,
                metadata={"cache_control": cc[:80]},
            ),
            confidence=0.75,
        )
    return None


def _check_rss_feed_absent(pg_html: str, page_url: str, root_url: str) -> "ScanFinding | None":
    """Flag homepage with blog/news nav links but no RSS/Atom feed discovery link (v31).

    Sites with blog or news sections that omit <link rel="alternate" type="application/rss+xml">
    miss content aggregators (Feedly, NewsBlur, Inoreader), podcast/news apps, Google News
    discovery, and email newsletter tools that auto-pull from RSS feeds. This is a free SEO
    plus audience retention signal that many SMB sites fail to set even when they actively blog.
    Only fires on the root URL to avoid false positives on inner blog listing pages.
    """
    if page_url != root_url:
        return None
    if not BLOG_NAV_HREF_RE.search(pg_html):
        return None  # No blog/news nav — not applicable
    if RSS_LINK_RE.search(pg_html):
        return None  # RSS discovery link already present
    blog_match = BLOG_NAV_HREF_RE.search(pg_html)
    return ScanFinding(
        category="seo",
        severity="low",
        title="Blog/news section detected but no RSS feed discovery link",
        description=(
            "Navigation links to a blog or news section were detected but the homepage has no "
            '<link rel="alternate" type="application/rss+xml"> element in the <head>. '
            "Without this, content aggregators (Feedly, Inoreader, Google News), RSS-driven "
            "social scheduling tools, and email automation platforms cannot auto-discover the feed. "
            "For service businesses that publish regular content, this is a missed distribution channel "
            "that costs nothing to fix and can generate passive audience growth over time."
        ),
        remediation=(
            "Add this tag inside every page <head>: "
            '<link rel="alternate" type="application/rss+xml" title="Blog Feed" href="/blog/feed.rss">. '
            "WordPress auto-generates feeds at /feed/ and /category/blog/feed/ — just add the link tag. "
            "For Webflow, Squarespace, or Wix: check your CMS feed URL then add the link tag via code injection. "
            "Validate your feed at https://validator.w3.org/feed/ after publishing."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=(blog_match.group(0) if blog_match else "")[:100],
            metadata={"blog_nav_detected": True, "rss_link_present": False},
        ),
        confidence=0.71,
    )


def _check_missing_twitter_card(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Flag pages with og:title but no twitter:card meta tag — plain-text Twitter/X previews (v31).

    When a URL is shared on Twitter/X, the platform looks for twitter:card meta tags to render
    a rich card preview (image, headline, description). Pages with og:title but no twitter:card
    fall back to a plain-text link — no preview image, no headline formatting. Studies show
    rich card links get 2–5× more clicks than plain-text links on Twitter/X. This fix takes
    under 5 minutes and requires adding one meta tag to every page <head>. Only fires when
    og:title is already present to avoid duplicating the general Open Graph finding.
    """
    if not OG_TITLE_RE.search(pg_html):
        return None  # No OG tags — different finding; skip to avoid noise
    if TWITTER_CARD_RE.search(pg_html):
        return None  # twitter:card already present
    return ScanFinding(
        category="seo",
        severity="low",
        title="Missing twitter:card meta tag — social shares render as plain-text links",
        description=(
            "This page has Open Graph (og:title) markup but is missing the twitter:card meta tag. "
            "When shared on Twitter/X, the page renders as a plain-text link without a preview image "
            "or headline card. Rich card previews receive 2–5× higher click-through rates than plain "
            "links on social platforms. For service businesses where prospects share content or research "
            "providers on social media, this is a missed conversion opportunity on every social share. "
            "Twitter/X will fall back to og:title/og:description values when twitter: equivalents are "
            "absent — so adding twitter:card alone enables full rich previews immediately."
        ),
        remediation=(
            'Add this meta tag to every page <head>: <meta name="twitter:card" content="summary_large_image">. '
            'Optionally add: <meta name="twitter:title" content="Page Title"> and '
            '<meta name="twitter:description" content="Your description (max 200 chars)">. '
            "Most CMS SEO plugins (Yoast, RankMath, All-in-One SEO) generate twitter: tags automatically "
            "when configured — check your SEO plugin settings for 'Social' or 'Twitter' tab. "
            "Validate your Twitter Card at https://cards-dev.twitter.com/validator after publishing."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet="og:title present; twitter:card absent",
            metadata={"og_title_present": True, "twitter_card_present": False},
        ),
        confidence=0.83,
    )


def _check_dns_caa_record(domain: str) -> "ScanFinding | None":
    """Flag domains without a DNS CAA record — TLS certificate issuance is unrestricted (v31).

    CAA (Certificate Authority Authorization) DNS records specify which CAs are permitted to
    issue TLS certificates for a domain. Without a CAA record, any of the 150+ publicly trusted
    CAs worldwide can issue a certificate — creating risk from CA compromise, social engineering
    of validation processes, or certificate misissuance going undetected in Certificate Transparency
    logs. Adding a CAA record is a 1-DNS-entry fix recommended by OWASP A05:2021 (Security
    Misconfiguration) and the CA/Browser Forum. This check uses dnspython (same dependency as
    email DNS auth) and does a single lightweight DNS query per iteration.
    """
    try:
        import dns.resolver  # type: ignore
    except Exception:
        return None  # dnspython unavailable — skip gracefully
    resolver = dns.resolver.Resolver()
    resolver.lifetime = 5.0
    resolver.timeout = 3.0
    try:
        resolver.resolve(domain, "CAA")
        return None  # CAA record exists — no finding
    except Exception:
        pass  # NXDOMAIN, NoAnswer, Timeout — treat as absent
    return ScanFinding(
        category="security",
        severity="low",
        title="No DNS CAA record — any Certificate Authority can issue TLS certs for this domain",
        description=(
            "This domain has no CAA (Certificate Authority Authorization) DNS record. "
            "CAA records restrict which CAs are permitted to issue TLS certificates for the domain. "
            "Without one, any of the 150+ publicly trusted CAs can be used — increasing risk from "
            "CA compromise, social engineering of validation processes, or accidental misissuance. "
            "OWASP A05:2021 (Security Misconfiguration) and the CA/Browser Forum both recommend "
            "CAA records for all publicly accessible domains. "
            "CAA records are logged in Certificate Transparency logs, making misissuance detectable "
            "— but only after the fact. Restricting issuance proactively eliminates the risk entirely."
        ),
        remediation=(
            "Add a DNS CAA record via your DNS provider (Cloudflare, Route 53, GoDaddy, Namecheap). "
            'Example for Let\'s Encrypt: Name=yourdomain.com, Type=CAA, Value: 0 issue "letsencrypt.org" '
            'and 0 issuewild "letsencrypt.org". '
            'For Cloudflare TLS: 0 issue "cloudflare.com". '
            "Add multiple CAA records if using more than one CA. "
            "Verify using: dig CAA yourdomain.com or https://dnschecker.org/#CAA/yourdomain.com. "
            "This is a 5-minute DNS change with no service disruption."
        ),
        evidence=WebsiteEvidence(
            page_url=f"https://{domain}/",
            metadata={"domain": domain, "caa_record_present": False},
        ),
        confidence=0.72,
    )


def _check_body_render_blocking_scripts(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Flag pages with ≥3 synchronous non-CDN scripts in the body — interactivity delay (v31).

    While v15 flags render-blocking scripts in <head> (which block first paint), synchronous
    <script src=...> tags without async/defer placed in the <body> delay Time to Interactive
    (TTI) — the moment when a user can first click a button, fill a form, or interact with
    any page element. Each blocking body script forces the browser to halt parsing, fetch the
    script from the origin server, execute it fully, then resume DOM parsing. For non-CDN
    scripts (site-hosted JS files), there is no edge-cache benefit to justify this penalty.
    Google's Lighthouse TTI metric directly affects Core Web Vitals scoring.
    Medium severity at ≥5 blocking body scripts (high interactivity delay risk).
    """
    body_match = re.search(r'<body[^>]*>(.*?)</body>', pg_html, re.IGNORECASE | re.DOTALL)
    if not body_match:
        return None
    body_html = body_match.group(1)
    blocking = NON_CDN_BODY_SCRIPT_RE.findall(body_html)
    count = len(blocking)
    if count < 3:
        return None
    severity = "medium" if count >= 5 else "low"
    return ScanFinding(
        category="performance",
        severity=severity,
        title=f"{count} synchronous body scripts delay page interactivity (Time to Interactive)",
        description=(
            f"Found {count} synchronous (non-async, non-defer) <script src=...> tags in the page body "
            "that are not loading from major CDN providers. Each one forces the browser to halt HTML "
            "parsing, download the script file from the origin server, and execute it fully before "
            "resuming — delaying Time to Interactive (TTI). For service businesses relying on contact "
            "forms and CTA buttons, delayed interactivity means prospects cannot click or submit until "
            "all scripts finish executing. Google's Core Web Vitals TBT (Total Blocking Time) metric "
            f"penalizes sites with multiple synchronous scripts. {count} blocking scripts detected."
        ),
        remediation=(
            "Add async or defer attribute to all non-critical <script> tags in the page body. "
            "Use defer for scripts that depend on the DOM (form handlers, analytics, chat widgets). "
            "Use async for fully independent scripts (A/B testing, heatmap tools, social widgets). "
            'Example: <script src="/assets/main.js" defer></script>. '
            "Scripts controlling above-fold visual rendering should be in <head> with defer. "
            "Use Chrome DevTools Coverage tab or PageSpeed Insights to identify and remove unused scripts."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            metadata={"blocking_body_script_count": count},
        ),
        confidence=0.73,
    )


def _check_input_type_validation(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect form inputs using generic type='text' for contact-named fields (v32).

    HTML5 semantic input types (email, tel, date, number) provide native browser validation,
    mobile-optimized keyboards, and browser autofill. Using type='text' for email, phone, or
    contact-named fields skips these benefits — increasing form abandonment and reducing data
    quality. Common in older WordPress themes and templates built before HTML5 adoption.
    """
    bad_inputs = INPUT_TEXT_NAMED_RE.findall(pg_html)
    if not bad_inputs:
        return None
    count = len(bad_inputs)
    severity = "medium" if count >= 3 else "low"
    return ScanFinding(
        category="conversion",
        severity=severity,
        title="Form inputs using generic type='text' for contact fields",
        description=(
            f"{count} form input field(s) detected with type='text' for email, phone, or "
            "contact-related fields. HTML5 semantic types (type='email', type='tel') activate "
            "native browser validation, appropriate mobile keyboards (numeric keypad for phone, "
            "@ keyboard for email), and autofill matching. Missing these reduces form completion "
            "rates and increases data-entry errors in submitted leads."
        ),
        remediation=(
            "Change input type attributes to semantic HTML5 values: use type='email' for "
            "email fields, type='tel' for phone numbers, type='date' for date inputs. "
            "In WordPress/Elementor: edit the form widget and update the input type dropdown. "
            "In raw HTML: change <input type='text' name='email'> to <input type='email' "
            "name='email'>. Takes under 5 minutes per field — no backend changes required."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=str(bad_inputs[0])[:200] if bad_inputs else "",
            metadata={"text_type_contact_inputs": count},
        ),
        confidence=0.76,
    )


def _check_missing_page_h1(pg_html: str, page_url: str, root_url: str) -> "ScanFinding | None":
    """Detect inner pages with zero H1 tags — foundational SEO requirement (v32).

    Every page should have exactly one H1 heading that describes the primary topic of that page.
    Missing H1 tags on inner pages reduce their ability to rank for their target keywords, as
    search engines use the H1 as a primary relevance signal alongside the title tag and content.
    Different from the generic-H1 check (v10) which fires when H1 IS present but is vague.
    """
    if page_url == root_url:
        return None  # Homepage H1 handled by existing generic-H1 and thin-content checks
    h1_count = len(H1_RE.findall(pg_html))
    if h1_count > 0:
        return None
    word_count = len(WORD_CONTENT_RE.findall(pg_html))
    if word_count < 100:
        return None  # Skip near-empty pages (error pages, stubs)
    return ScanFinding(
        category="seo",
        severity="medium",
        title="Inner page missing H1 heading tag",
        description=(
            "This page has no H1 heading tag, which is a foundational on-page SEO element. "
            "Search engines use H1 tags as a primary relevance signal to understand the main "
            "topic of a page. Without an H1, this page is less likely to rank for its intended "
            "keywords and misses a high-value on-page optimization that takes under 2 minutes to add."
        ),
        remediation=(
            "Add one H1 heading to this page that clearly describes its primary topic or target "
            "keyword phrase. The H1 should be unique across all pages, match the intent of the "
            "page content, and ideally include the primary keyword you want this page to rank for. "
            "In WordPress: edit the page and add a 'Heading' block set to H1. Use CSS to style "
            "rather than demoting heading levels for visual reasons. Verify with Google Search "
            "Console URL Inspection that the heading is visible in the rendered HTML."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet="No <h1> tag detected on this inner page",
            metadata={"h1_count": 0, "word_count": word_count},
        ),
        confidence=0.88,
    )


def _check_duplicate_h2_headings(pages: dict[str, str]) -> "ScanFinding | None":
    """Detect the same H2 heading text on 3+ different pages — thin content signal (v32).

    Identical H2 headings across multiple pages indicates templated or thin content where pages
    lack unique structural identity. Search engines may interpret shared heading structures as
    thin/duplicate content, reducing each page's ability to rank distinctly for different keyword
    targets. Common in sites using repeated section templates across service or location pages.
    """
    h2_pages: dict[str, list[str]] = {}
    for url, html in pages.items():
        raw_h2s = H2_CONTENT_RE.findall(html)
        for raw_h2 in raw_h2s:
            clean = re.sub(r'<[^>]+>', '', raw_h2).strip().lower()
            clean = re.sub(r'\s+', ' ', clean)
            if len(clean) < 5:
                continue
            h2_pages.setdefault(clean, []).append(url)
    duplicates = {k: v for k, v in h2_pages.items() if len(set(v)) >= 3}
    if not duplicates:
        return None
    worst_h2 = max(duplicates, key=lambda k: len(set(duplicates[k])))
    worst_urls = list(dict.fromkeys(duplicates[worst_h2]))  # dedupe, preserve order
    return ScanFinding(
        category="seo",
        severity="low",
        title="Duplicate H2 headings detected across multiple pages",
        description=(
            f"The H2 heading '{worst_h2[:60]}' appears on {len(worst_urls)} pages "
            f"({', '.join(worst_urls[:2])}{' +more' if len(worst_urls) > 2 else ''}). "
            f"{len(duplicates)} distinct H2 text(s) are shared across 3+ pages, suggesting "
            "templated page structures with insufficient content differentiation. Each page "
            "should have unique heading structure to rank distinctly for its own keyword targets."
        ),
        remediation=(
            "Review pages with shared H2 headings and differentiate them to reflect each "
            "page's unique topic and target keywords. Instead of generic H2s like 'Our Services' "
            "or 'Contact Us' appearing identically on every page, use specific keyword-targeted "
            "headings describing the unique value on that page. Use Screaming Frog to export and "
            "audit all headings across the site for cross-page heading duplication patterns."
        ),
        evidence=WebsiteEvidence(
            page_url=worst_urls[0],
            snippet=f"H2 '{worst_h2[:60]}' on {len(worst_urls)} pages",
            metadata={"duplicate_h2_count": len(duplicates), "worst_page_count": len(worst_urls)},
        ),
        confidence=0.72,
    )


def _check_nav_aria_label(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect multiple nav elements without distinct aria-label attributes (v32).

    When a page has more than one <nav> element, screen reader users need aria-label attributes
    to distinguish between them (e.g., 'Main Navigation' vs 'Footer Navigation'). Without
    unique labels, screen readers announce each as just 'navigation' with no way to differentiate
    the primary menu from the footer nav or sidebar links. WCAG 2.4.1 (Bypass Blocks), 1.3.6.
    """
    nav_count = len(NAV_ELEMENT_RE.findall(pg_html))
    if nav_count < 2:
        return None
    labeled_count = len(NAV_ARIA_LABEL_RE.findall(pg_html))
    if labeled_count >= nav_count:
        return None
    unlabeled = nav_count - labeled_count
    return ScanFinding(
        category="ada",
        severity="low",
        title="Multiple nav elements missing distinct aria-label attributes",
        description=(
            f"{nav_count} navigation landmark(s) detected on this page, but only "
            f"{labeled_count} have aria-label or aria-labelledby attributes. "
            "Screen reader users rely on landmark labels to distinguish navigation regions "
            "and skip to relevant content. Without labels, screen readers announce each as "
            "just 'navigation', making it impossible to differentiate the main menu from the "
            "footer nav or sidebar navigation. WCAG 2.4.1, WCAG 1.3.6."
        ),
        remediation=(
            "Add descriptive aria-label attributes to all <nav> elements: "
            "<nav aria-label='Main Navigation'>, <nav aria-label='Footer Navigation'>, "
            "<nav aria-label='Breadcrumb'>. Each label should briefly describe the "
            "navigation's purpose. In WordPress themes: add aria-label to the wp_nav_menu "
            "container_class or use a theme hook. Takes under 10 minutes and significantly "
            "improves screen reader usability for keyboard-only visitors."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=f"{nav_count} <nav> elements found, {unlabeled} missing aria-label",
            metadata={"nav_count": nav_count, "labeled_count": labeled_count},
        ),
        confidence=0.79,
    )


def _check_meta_robots_nofollow(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect meta robots nofollow on public pages — silently blocks link equity flow (v32).

    A <meta name='robots' content='nofollow'> tag instructs search engines not to follow any
    links on the page, cutting off PageRank distribution from that page to every linked page.
    This is almost never intentional on public-facing pages and silently undermines the site's
    internal linking strategy. Distinct from noindex (already checked separately) — nofollow
    allows indexing but blocks link equity, which is particularly damaging for sites relying on
    internal links to pass authority to service or product pages.
    """
    if not ROBOTS_NOFOLLOW_RE.search(pg_html):
        return None
    # Skip if noindex is also set — the noindex check already flags this page
    if NOINDEX_RE.search(pg_html):
        return None
    return ScanFinding(
        category="seo",
        severity="low",
        title="Meta robots nofollow blocking link equity on page",
        description=(
            "A 'nofollow' directive in the meta robots tag is preventing search engines from "
            "following links on this page, blocking PageRank from flowing to linked pages. "
            "This is rarely intentional on public pages and silently undermines the site's "
            "internal linking structure, which affects how well inner pages rank for their "
            "target keywords. Unlike noindex, nofollow allows the page itself to be indexed "
            "but cuts off equity distribution — a subtle but impactful SEO misconfiguration."
        ),
        remediation=(
            "Check the page template for <meta name='robots' content='nofollow'> or "
            "content='none'. If this page should pass link equity to linked pages, remove "
            "the nofollow directive or change to content='index,follow'. In WordPress: check "
            "Yoast SEO or Rank Math settings for 'Advanced' meta robots on this specific page. "
            "Use Google Search Console URL Inspection to verify Google can follow links from "
            "this page after the change."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet="<meta name='robots' content='...nofollow...'> detected",
            metadata={"nofollow_present": True},
        ),
        confidence=0.84,
    )


def _check_x_content_type_options(response_headers: dict, page_url: str) -> "ScanFinding | None":
    """Detect missing X-Content-Type-Options: nosniff header — MIME-type sniffing attack vector (v33).

    Without this header, browsers may interpret a response with a different MIME type than
    declared — allowing attackers to serve malicious scripts or HTML disguised as benign resources
    (e.g., images or JSON). This is a low-effort, high-confidence fix that should be on every
    server: a single server-config line. OWASP categorises this under A05:2021 Security Misconfiguration.
    """
    value = str(response_headers.get(X_CONTENT_TYPE_OPTIONS_KEY, "")).strip().lower()
    if "nosniff" in value:
        return None
    return ScanFinding(
        category="security",
        severity="low",
        title="X-Content-Type-Options: nosniff header missing",
        description=(
            "The X-Content-Type-Options: nosniff HTTP header is absent. Without it, browsers "
            "may 'sniff' the content type of responses and execute files as a different MIME "
            "type than declared — a technique attackers exploit to run malicious scripts or "
            "HTML embedded in non-HTML responses. This header costs nothing to add and is "
            "recommended by OWASP A05:2021 Security Misconfiguration as a baseline hardening "
            "measure. Many security scanners will flag its absence as a failing check."
        ),
        remediation=(
            "Add the following header to your server or CDN configuration: "
            "X-Content-Type-Options: nosniff. "
            "In nginx.conf: add_header X-Content-Type-Options 'nosniff' always; "
            "In Apache .htaccess: Header always set X-Content-Type-Options nosniff "
            "In Cloudflare: use a Transform Rule to inject the header — no server access required. "
            "Takes under 5 minutes and requires no code changes. "
            "Verify with securityheaders.com after deploying."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet="X-Content-Type-Options header absent from HTTP response",
            metadata={"header": "X-Content-Type-Options", "expected": "nosniff", "found": "absent"},
        ),
        confidence=0.92,
    )


def _check_permissions_policy(response_headers: dict, page_url: str) -> "ScanFinding | None":
    """Detect missing Permissions-Policy (or legacy Feature-Policy) header (v33).

    The Permissions-Policy header lets the site declare which browser APIs (camera, microphone,
    geolocation, payment) third-party scripts are allowed to access. Without it, any embedded
    third-party iframe or script — including ad networks and chat widgets — may request access
    to sensitive device APIs without the site owner's knowledge. This is a privacy-first signal
    and increasingly expected by enterprise buyers and compliance-conscious SMBs.
    """
    has_policy = any(
        PERMISSIONS_POLICY_KEY_RE.match(k) for k in response_headers
    )
    if has_policy:
        return None
    return ScanFinding(
        category="security",
        severity="low",
        title="Permissions-Policy header not set — browser APIs unrestricted",
        description=(
            "No Permissions-Policy (or Feature-Policy) header is present. Without this header, "
            "any embedded third-party script or iframe on your site — including ad networks, "
            "chat widgets, and analytics — may request access to sensitive browser APIs such as "
            "the user's camera, microphone, and geolocation without your explicit consent. "
            "This is a growing concern for businesses whose websites embed multiple third-party "
            "tools, and will be flagged by browser security audits and enterprise security reviews."
        ),
        remediation=(
            "Add a Permissions-Policy header to restrict API access to only what your site "
            "legitimately needs. A safe default for most SMB sites: "
            "Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(). "
            "In nginx.conf: add_header Permissions-Policy 'camera=(), microphone=(), geolocation=()' always; "
            "In Apache .htaccess: Header always set Permissions-Policy 'camera=(), microphone=()' "
            "In Cloudflare: use a Modify Response Headers transform rule — no server access required. "
            "Ask your developer or hosting provider — this is a 5-minute configuration change."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet="Permissions-Policy (Feature-Policy) header absent from HTTP response",
            metadata={"header": "Permissions-Policy", "found": "absent"},
        ),
        confidence=0.71,
    )


def _check_missing_og_image(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect og:title present but og:image missing — incomplete Open Graph metadata (v33).

    When a page is shared on LinkedIn, Facebook, or Slack, the platform pulls the og:image
    to render a visual preview card. Pages with og:title but no og:image show a grey placeholder
    box instead, drastically reducing click-through rates from social shares. This is especially
    impactful for service businesses where referrals and social word-of-mouth drive leads.
    """
    # Only fire if og:title is present (partial OG setup, not a site with no OG at all)
    if not OG_TITLE_RE.search(pg_html):
        return None
    if OG_IMAGE_RE.search(pg_html):
        return None
    return ScanFinding(
        category="seo",
        severity="low",
        title="og:image missing — social share previews show blank placeholder",
        description=(
            "An og:title tag is present but no og:image is set. When this page is shared on "
            "LinkedIn, Facebook, or Slack, the social platform will render a plain text card "
            "with a grey placeholder box instead of a branded image — reducing the likelihood "
            "that anyone clicks the shared link. For service businesses that rely on referrals "
            "and word-of-mouth, this is a missed brand impression every time someone shares "
            "your URL. This is a quick fix with no code changes required."
        ),
        remediation=(
            "Add an og:image meta tag to the page <head>: "
            "<meta property='og:image' content='https://yourdomain.com/og-image.jpg'>. "
            "The image should be at least 1200×630px. Use a branded banner or hero image. "
            "In WordPress with Yoast SEO: go to the page editor → Yoast SEO panel → Social tab "
            "→ upload a Facebook image. In Squarespace: Page Settings → Social Image. "
            "Takes under 5 minutes. Validate with https://developers.facebook.com/tools/debug/ "
            "after publishing."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet="og:title present; og:image absent from <head>",
            metadata={"og_title_present": True, "og_image_present": False},
        ),
        confidence=0.83,
    )


def _check_link_underline_suppressed(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect broad link underline suppression without hover restore — WCAG 1.4.1 violation (v33).

    When a site's CSS removes the underline from all links (text-decoration: none) without
    providing a visual hover indicator, sighted users who rely on colour alone cannot distinguish
    links from surrounding body text. WCAG 1.4.1 (Use of Color) requires that colour is not the
    only means of distinguishing links from non-link text. This is especially problematic for
    users with colour vision deficiency or low-contrast display settings.
    """
    # Look for broad link text-decoration suppression inside <style> blocks
    style_blocks = re.findall(r'<style[^>]*>(.*?)</style>', pg_html, re.IGNORECASE | re.DOTALL)
    style_text = " ".join(style_blocks)
    if not style_text:
        return None
    if not LINK_NODECOR_RE.search(style_text):
        return None
    # If hover restore is present, links ARE visually distinguishable on interaction
    if LINK_HOVER_RESTORE_RE.search(style_text):
        return None
    return ScanFinding(
        category="ada",
        severity="low",
        title="Link underlines suppressed — links indistinguishable from body text",
        description=(
            "The site's stylesheet applies 'text-decoration: none' to anchor elements (links) "
            "without providing an alternative visual indicator on hover. This means links are "
            "distinguishable from surrounding text only by colour. Users with colour vision "
            "deficiency (affecting ~8% of men) and users on high-brightness or low-contrast "
            "displays may not be able to identify clickable text at all. WCAG 1.4.1 (Use of "
            "Color) requires that colour is not the only visual means of conveying information, "
            "which includes distinguishing links from non-link text."
        ),
        remediation=(
            "Either restore the default underline (remove 'text-decoration: none' from the "
            "base 'a' selector) or add a clear visual indicator that does NOT rely solely on "
            "colour — for example: a:hover { text-decoration: underline; } or add a visible "
            "bottom border to links. If design constraints require hiding underlines, use a "
            "high-contrast colour + bold weight combination AND ensure the hover/focus state "
            "adds an underline or border. Test with Chrome's DevTools accessibility checker "
            "or the axe browser extension."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet="a { text-decoration: none } detected in <style> block; no :hover restore found",
            metadata={"wcag": "1.4.1", "issue": "link_underline_suppressed"},
        ),
        confidence=0.74,
    )


def _check_empty_alt_link_images(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect linked images with empty alt text — screen reader announces 'empty link' (v33).

    When a hyperlink contains only an image with alt="" and no aria-label on the link itself,
    screen readers announce the link as "link" or "empty" with no destination information.
    This violates WCAG 4.1.2 (Name, Role, Value) and WCAG 2.4.4 (Link Purpose), making
    navigation impossible for blind users — a significant ADA exposure for US-based businesses.
    """
    matches = IMG_EMPTY_ALT_IN_LINK_RE.findall(pg_html)
    count = len(matches)
    if count == 0:
        return None
    severity = "medium" if count >= 2 else "low"
    return ScanFinding(
        category="ada",
        severity=severity,
        title=f"Linked image{'s' if count > 1 else ''} with empty alt text — {count} empty link{'s' if count > 1 else ''} for screen readers",
        description=(
            f"Found {count} image link{'s' if count > 1 else ''} where the <img> has alt=\"\" (empty) "
            "and the parent <a> has no aria-label. Screen readers will announce these as 'link' "
            "with no destination context, making navigation impossible for blind users. This "
            "violates WCAG 4.1.2 (Name, Role, Value) and WCAG 2.4.4 (Link Purpose) — two "
            "Level A requirements under WCAG 2.1. For US-based businesses, ADA Title III "
            "lawsuits have specifically cited empty link text as a barrier to access."
        ),
        remediation=(
            "For each image-only link, add descriptive alt text to the img element describing "
            "the link destination: <a href='/about'><img src='logo.png' alt='About Us'></a>. "
            "Alternatively, add aria-label to the anchor: <a href='/about' aria-label='About Us'>. "
            "Never use alt='' on an image that is the only content inside a link — reserve "
            "empty alt only for purely decorative images that are NOT inside links. "
            "Validate using the axe DevTools extension (free) or NVDA screen reader after fixing."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=f"<a href=...><img ... alt=\"\"> detected — {count} instance{'s' if count > 1 else ''}",
            metadata={"wcag": "4.1.2", "empty_link_image_count": count},
        ),
        confidence=0.85,
    )


def _check_font_display_swap(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect Google Fonts loaded without display=swap — FOIT risk (v34).

    When Google Fonts are loaded via <link> or @import without the display=swap parameter,
    browsers use FOIT (Flash of Invisible Text) — text is invisible while the font loads.
    On slow connections this can keep text invisible for 3+ seconds, hurting both user
    experience and Core Web Vitals (CLS / LCP). Adding ?display=swap makes text immediately
    readable in the fallback font while the custom font loads in the background.
    """
    font_links = FONT_DISPLAY_SWAP_RE.findall(pg_html)
    if not font_links:
        return None
    # Check if all font links already include display=swap
    missing_swap = [lnk for lnk in font_links if not FONT_DISPLAY_SWAP_PARAM_RE.search(lnk)]
    if not missing_swap:
        return None
    count = len(missing_swap)
    return ScanFinding(
        category="performance",
        severity="low",
        title=f"Google Fonts loaded without font-display:swap — text invisible during load ({count} link{'s' if count > 1 else ''})",
        description=(
            f"Found {count} Google Fonts link{'s' if count > 1 else ''} without the display=swap parameter. "
            "Browsers using FOIT (Flash of Invisible Text) keep text invisible while the font downloads. "
            "On a typical 3G connection this can hide body text for 2–5 seconds, increasing bounce rate "
            "and degrading your Core Web Vitals Largest Contentful Paint (LCP) score — a direct Google "
            "ranking signal. Your visitors see a blank page where text should be."
        ),
        remediation=(
            "Add &display=swap to each Google Fonts URL: "
            "https://fonts.googleapis.com/css2?family=Roboto&display=swap. "
            "In WordPress, install the Swap Google Fonts Display plugin (free, 1-click). "
            "In Elementor: Appearance > Theme File Editor is not needed — use a plugin like "
            "'OMGF | GDPR/DSGVO Compliant, Faster Google Fonts' to self-host and swap in one step. "
            "Verify in PageSpeed Insights: look for 'Ensure text remains visible during webfont load'."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=missing_swap[0][:120] if missing_swap else "",
            metadata={"wcag": "n/a", "cwv_signal": "LCP/CLS", "missing_swap_count": count},
        ),
        confidence=0.81,
    )


def _check_button_accessible_name(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect buttons with no accessible name — WCAG 4.1.2 violation (v34).

    Buttons that lack visible text, aria-label, and title attributes cannot be understood
    by screen readers or voice control users. Screen readers announce them as 'button'
    with no context. This is a WCAG 4.1.2 Level A violation (Name, Role, Value) —
    one of the most commonly cited ADA lawsuit triggers.
    """
    # Find all button tags with their attributes and inner content
    button_pattern = re.compile(
        r'<button\b([^>]*)>([\s\S]*?)</button>',
        re.IGNORECASE,
    )
    matches = button_pattern.findall(pg_html)
    unnamed_count = 0
    for attrs, inner in matches:
        attrs_lower = attrs.lower()
        inner_stripped = re.sub(r'<[^>]+>', '', inner).strip()
        has_aria_label = 'aria-label=' in attrs_lower
        has_title = 'title=' in attrs_lower
        has_aria_labelledby = 'aria-labelledby=' in attrs_lower
        has_text = bool(inner_stripped)
        # Check if inner has an img with descriptive alt (not empty)
        img_alt = re.search(r'<img\b[^>]*\balt=["\']([^"\']+)["\']', inner, re.IGNORECASE)
        has_img_alt = bool(img_alt)
        if not has_text and not has_aria_label and not has_title and not has_aria_labelledby and not has_img_alt:
            unnamed_count += 1
    if unnamed_count == 0:
        return None
    severity = "medium" if unnamed_count >= 2 else "low"
    return ScanFinding(
        category="ada",
        severity=severity,
        title=f"Button{'s' if unnamed_count > 1 else ''} without accessible name — {unnamed_count} unnamed button{'s' if unnamed_count > 1 else ''}",
        description=(
            f"Found {unnamed_count} <button> element{'s' if unnamed_count > 1 else ''} with no visible text, "
            "no aria-label, and no title attribute. Screen readers will announce these as 'button' "
            "with no indication of their purpose — keyboard and voice control users cannot determine "
            "what the button does. This violates WCAG 4.1.2 (Name, Role, Value) Level A. "
            "Common examples include icon buttons (hamburger menus, close buttons, sliders) "
            "that rely solely on visual icon placement for meaning."
        ),
        remediation=(
            "Add descriptive text or an aria-label to every button: "
            "<button aria-label='Open navigation menu'>☰</button>. "
            "For icon-only buttons in WordPress/Elementor, use the 'Icon Box' widget with visible text, "
            "or add custom CSS that positions the label off-screen for sighted users while keeping it "
            "accessible: .sr-only { position:absolute; width:1px; overflow:hidden; }. "
            "Test using NVDA (free) or VoiceOver (built into Mac/iOS): Tab to each button and confirm "
            "the announced name matches its visual purpose. Takes under 30 minutes for most sites."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=f"<button> without accessible name — {unnamed_count} instance{'s' if unnamed_count > 1 else ''}",
            metadata={"wcag": "4.1.2", "unnamed_button_count": unnamed_count},
        ),
        confidence=0.87,
    )


def _check_price_schema_missing(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect pricing content without Offer/Product JSON-LD schema — missed rich results (v34).

    When a page displays prices but lacks Offer or Product structured data, Google cannot
    show price rich results (price chips, product cards) in SERPs. For service businesses
    this means losing star ratings and pricing display in organic search — a visibility gap
    vs. competitors who have implemented schema. Fires on any page with price signals.
    """
    if not PRICE_TEXT_RE.search(pg_html):
        return None
    if OFFER_SCHEMA_RE.search(pg_html):
        return None
    return ScanFinding(
        category="seo",
        severity="low",
        title="Pricing content detected without Offer/Product schema markup",
        description=(
            "This page contains pricing information ($X/month, starting at $X, etc.) but lacks "
            "Offer or Product JSON-LD structured data. Without this schema, Google cannot display "
            "price chips or product cards in search results — a rich result feature that can increase "
            "click-through rate by 20–30% for competitors who have it. Your pricing is invisible "
            "to Google's rich result eligibility checks."
        ),
        remediation=(
            "Add a Product or Offer JSON-LD block to the page head: "
            '{"@context":"https://schema.org","@type":"Service","name":"Your Service Name",'
            '"offers":{"@type":"Offer","price":"XX","priceCurrency":"USD"}}. '
            "In WordPress: use Rank Math (free) or Yoast SEO Premium — both include schema generators. "
            "In Squarespace or Wix: add a Code Block in the page header and paste the JSON-LD. "
            "Validate your schema at https://search.google.com/test/rich-results before publishing."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=PRICE_TEXT_RE.search(pg_html).group(0)[:80] if PRICE_TEXT_RE.search(pg_html) else "",
            metadata={"schema_type": "Offer/Product", "wcag": "n/a"},
        ),
        confidence=0.67,
    )


def _check_cookie_prefix_security(response_headers: dict, page_url: str) -> "ScanFinding | None":
    """Detect session/auth cookies missing __Secure- or __Host- prefix — cookie injection risk (v34).

    Cookies named 'session', 'auth', 'login', 'token', or 'user' without the __Secure- or __Host-
    prefix can be overwritten by subdomains or insecure channels, enabling session fixation and
    cookie injection attacks (OWASP A02:2021 Cryptographic Failures, A07:2021 Auth Failures).
    """
    set_cookie_headers: list[str] = []
    for key, val in response_headers.items():
        if key.lower() == "set-cookie":
            if isinstance(val, list):
                set_cookie_headers.extend(str(v) for v in val)
            else:
                set_cookie_headers.append(str(val))
    if not set_cookie_headers:
        return None
    # Identify session/auth-named cookies lacking __Secure- or __Host- prefix
    vulnerable_cookies: list[str] = []
    for cookie in set_cookie_headers:
        cookie_name_match = re.match(r'\s*([^=\s;]+)\s*=', cookie)
        if not cookie_name_match:
            continue
        name = cookie_name_match.group(1)
        if COOKIE_SESSION_NAME_RE.match(name + "=") and not COOKIE_SECURE_PREFIX_RE.match(name):
            vulnerable_cookies.append(name)
    if not vulnerable_cookies:
        return None
    names_str = ", ".join(vulnerable_cookies[:3])
    return ScanFinding(
        category="security",
        severity="low",
        title=f"Session cookie{'s' if len(vulnerable_cookies) > 1 else ''} missing __Secure- prefix — cookie injection risk ({names_str})",
        description=(
            f"Found {len(vulnerable_cookies)} cookie{'s' if len(vulnerable_cookies) > 1 else ''} "
            f"({names_str}) with session/authentication-related names that lack the __Secure- or "
            "__Host- prefix. Without these prefixes, subdomains or HTTP responses can overwrite "
            "these cookies — enabling session fixation attacks where an attacker forces a victim "
            "to use a known session ID. OWASP A07:2021 Authentication Failures specifically "
            "recommends __Secure- and __Host- prefixes for any authentication-bearing cookie."
        ),
        remediation=(
            "Rename authentication cookies to use the __Secure- prefix: e.g., __Secure-session. "
            "For full subdomain isolation, use __Host- prefix (also requires Path=/ and no Domain= attribute). "
            "In PHP: session_name('__Secure-PHPSESSID'); ini_set('session.cookie_secure', '1'); "
            "ini_set('session.cookie_httponly', '1'); ini_set('session.cookie_samesite', 'Lax'). "
            "For WordPress: add define('COOKIE_SECURE_KEY', '__Secure-wordpress_logged_in') in wp-config.php. "
            "Verify using Chrome DevTools > Application > Cookies after making the change."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=f"Set-Cookie: {vulnerable_cookies[0]}=... (no __Secure- prefix)",
            metadata={"owasp": "A07:2021", "vulnerable_cookie_names": vulnerable_cookies[:5]},
        ),
        confidence=0.73,
    )


def _check_preload_key_requests(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect absence of <link rel='preload'> for LCP-critical resources (v34).

    Pages that load large hero images or custom fonts but use no preload hints force the browser
    to discover these resources only after parsing the full HTML. This delays the Largest
    Contentful Paint (LCP) — a Core Web Vitals metric and direct Google ranking factor.
    Only fires when: (a) no preload tags exist at all, AND (b) large image or font signals present.
    """
    if PRELOAD_LINK_RE.search(pg_html):
        return None  # Preload hints already present — no issue
    # Check for signals that LCP-critical resources are present
    has_hero_image = bool(re.search(
        r'<img\b[^>]*(?:class|id)=["\'][^"\']*(?:hero|banner|header|splash|cover|featured)[^"\']*["\']',
        pg_html, re.IGNORECASE,
    ))
    has_custom_font = bool(FONT_DISPLAY_SWAP_RE.search(pg_html) or re.search(
        r'@font-face\s*\{', pg_html, re.IGNORECASE,
    ))
    has_large_image_path = bool(re.search(
        r'<img\b[^>]*src=["\'][^"\']*(?:hero|banner|splash|cover|og[-_]image|bg[-_]image|background)[^"\']*["\']',
        pg_html, re.IGNORECASE,
    ))
    if not (has_hero_image or has_custom_font or has_large_image_path):
        return None
    signal = "hero image" if (has_hero_image or has_large_image_path) else "custom font"
    return ScanFinding(
        category="performance",
        severity="low",
        title=f"No resource preload hints — {signal} loads late, delaying LCP",
        description=(
            f"This page contains a {signal} that is likely your Largest Contentful Paint (LCP) "
            "element, but no <link rel='preload'> hints exist. The browser cannot discover and "
            "start downloading critical resources until after the HTML is parsed — delaying "
            "the LCP metric by 200–800ms on average. Google uses LCP as a direct ranking signal "
            "in Core Web Vitals (Good: <2.5s, Needs Improvement: 2.5–4s, Poor: >4s). "
            "Your visitors experience a visible delay before the most important content appears."
        ),
        remediation=(
            f"Add a preload hint in the <head> for your {signal}: "
            "<link rel='preload' href='/images/hero.jpg' as='image'> "
            "or for fonts: <link rel='preload' href='/fonts/brand.woff2' as='font' crossorigin>. "
            "In WordPress: install 'LiteSpeed Cache' or 'WP Rocket' (paid) which auto-detect and "
            "add preload hints for above-the-fold images. "
            "In Cloudflare: enable 'Polish' and 'Mirage' for automatic image optimization. "
            "Validate using PageSpeed Insights (free): look for 'Preload Largest Contentful Paint image'."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=f"No <link rel='preload'> found; {signal} detected without preload",
            metadata={"cwv_signal": "LCP", "has_hero_image": has_hero_image, "has_custom_font": has_custom_font},
        ),
        confidence=0.68,
    )


def _check_spf_too_many_lookups(spf_record: str, domain: str) -> "ScanFinding | None":
    """Detect SPF records with >10 DNS lookups — causes 'PermError' email auth failures (v35).

    RFC 7208 §4.6.4 mandates a hard limit of 10 DNS lookups during SPF evaluation.
    Records that exceed this limit result in a PermError on receiving mail servers,
    which many treat as a fail — silently killing legitimate email delivery without
    any bounce. This is a particularly insidious problem because it does not affect
    sending servers directly but fails only on the recipient's end.
    """
    if not spf_record or not spf_record.strip().lower().startswith("v=spf1"):
        return None
    mechanisms = SPF_LOOKUP_MECHANISM_RE.findall(spf_record)
    count = len(mechanisms)
    if count <= 10:
        return None
    return ScanFinding(
        category="email_auth",
        severity="medium",
        title=f"SPF record exceeds 10 DNS lookup limit ({count} lookups) — PermError risk",
        description=(
            f"Your SPF record contains {count} DNS lookup mechanisms "
            f"(include:, a, mx, ptr, exists, redirect), exceeding the RFC 7208 hard limit of 10. "
            "When receiving servers evaluate your SPF, they hit a PermError and may treat "
            "legitimate emails as failed authentication — silently blocking delivery without "
            "a bounce notification. Your newsletters, client emails, and sales outreach "
            "may be landing in spam or being rejected at major providers (Gmail, Outlook, Yahoo)."
        ),
        remediation=(
            "Audit and flatten your SPF record by replacing 'include:' chains with direct IP ranges. "
            "Step 1: Use MXToolbox SPF Record Lookup (free) to list all resolved mechanisms. "
            "Step 2: Replace frequently-nested includes (e.g., include:_spf.google.com) with "
            "their resolved IP4: ranges directly in your record. "
            "Step 3: Remove any sending services you no longer use. "
            "Step 4: Verify the flattened record has ≤10 lookups using MXToolbox and send a "
            "test email to mail-tester.com to confirm full SPF pass. "
            "Target: reduce to ≤8 lookups to leave buffer for future service additions."
        ),
        evidence=WebsiteEvidence(
            page_url=f"https://{domain}",
            snippet=spf_record[:200],
            metadata={"spf_lookup_count": count, "rfc": "RFC 7208 §4.6.4", "domain": domain},
        ),
        confidence=0.83,
    )


def _check_page_title_length(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect page titles that are too long (truncated in SERPs) or too short (weak signal) (v35).

    Google displays page titles up to ~60 characters in desktop SERPs. Longer titles are
    truncated with '...', hiding important keywords and reducing CTR. Very short titles
    (<15 characters) often indicate a placeholder or template value with no keyword signal.
    """
    title_match = TITLE_CONTENT_RE.search(pg_html)
    if not title_match:
        return None
    raw_title = title_match.group(1).strip()
    # Strip HTML tags inside title (rare but happens)
    title_text = re.sub(r'<[^>]+>', '', raw_title).strip()
    length = len(title_text)
    if 15 <= length <= 60:
        return None  # Within acceptable range
    if length > 75:
        severity = "medium"
        problem = f"severely truncated at {length} characters (Google shows ~60)"
        impact = "The most important keywords at the end of your title are hidden in search results, reducing click-through rate."
    elif length > 60:
        severity = "low"
        problem = f"slightly long at {length} characters (Google shows ~60)"
        impact = "Your title may be truncated in SERPs, hiding end keywords from searchers."
    else:
        # < 15 chars
        severity = "low"
        problem = f"very short at {length} characters — likely placeholder or missing brand context"
        impact = "A title this short provides minimal keyword signal to Google and gives searchers no context about the page."
    return ScanFinding(
        category="seo",
        severity=severity,
        title=f"Page title {problem}",
        description=(
            f"Page title is {length} characters: \"{title_text[:80]}{'...' if len(title_text) > 80 else ''}\". "
            f"{impact} "
            "Google's SERP title display limit is approximately 60 characters on desktop — titles "
            "longer than this are cut off with '...' before users see your most important keywords."
        ),
        remediation=(
            "Rewrite the page title to 50–60 characters. "
            "Format: [Primary Keyword] | [Secondary Keyword] — [Brand Name]. "
            "Put the most important keyword first (Google gives more weight to earlier terms). "
            "In WordPress: edit via Yoast SEO or Rank Math 'SEO Title' field — both show "
            "a live character counter and SERP preview so you can see exactly how it will appear. "
            "In Squarespace/Wix: use the SEO settings panel for each page. "
            "After publishing, check Google Search Console > Performance > Pages to track CTR changes."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=f'<title>{title_text[:80]}{"..." if len(title_text) > 80 else ""}</title>',
            metadata={"title_length": length, "title_preview": title_text[:60]},
        ),
        confidence=0.88,
    )


def _check_apple_touch_icon_missing(pg_html: str, page_url: str, root_url: str) -> "ScanFinding | None":
    """Detect missing apple-touch-icon link tag — degrades iOS homescreen and bookmark UX (v35).

    When iOS users save a website to their home screen or bookmark it in Safari,
    the browser looks for an apple-touch-icon to use as the app icon. Without it,
    iOS generates a blurry, low-quality screenshot thumbnail. For local businesses
    and service providers, a branded home screen icon reinforces professional trust.
    """
    if page_url.rstrip("/") != root_url.rstrip("/"):
        return None
    if APPLE_TOUCH_ICON_RE.search(pg_html):
        return None
    return ScanFinding(
        category="performance",
        severity="low",
        title="Apple touch icon missing — iOS home screen shows blurry screenshot",
        description=(
            "Your site does not include an <link rel='apple-touch-icon'> tag. "
            "When visitors on iPhone or iPad save your site to their home screen (common for "
            "repeat visitors and local service customers), iOS falls back to generating a "
            "blurry screenshot thumbnail instead of a crisp branded icon. "
            "For mobile-first businesses, this undermines professional brand perception "
            "and makes it harder for customers to re-open your site from their home screen."
        ),
        remediation=(
            "Create a 180×180px PNG icon with your brand logo on a solid background color. "
            "In WordPress: add to your theme's <head> via functions.php or use the 'Site Icon' "
            "setting in Appearance > Customize (Customizer auto-generates the touch icon). "
            "Manual method: <link rel='apple-touch-icon' sizes='180x180' href='/apple-touch-icon.png'>. "
            "Place the icon file in your website root directory. "
            "Verify with Chrome DevTools: open Application > Manifest to confirm icon detection. "
            "Takes under 15 minutes and improves brand recognition for all return iOS visitors."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet="<link rel='apple-touch-icon'> tag not found in <head>",
            metadata={"wcag": "n/a", "platform": "iOS Safari / Chrome on iOS"},
        ),
        confidence=0.72,
    )


def _check_form_spam_protection_absent(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect contact/inquiry forms with no spam protection signals (v35).

    Forms without bot protection (reCAPTCHA, hCaptcha, Turnstile, or honeypot fields)
    are routinely discovered and abused by spam bots within days of launch. This leads
    to hundreds of fake form submissions, injecting SEO spam into auto-reply emails,
    cluttering the inbox, and potentially causing email delivery issues if the owner's
    IP is flagged for spam sending patterns.
    """
    has_form = re.search(r'<form\b', pg_html, re.IGNORECASE)
    if not has_form:
        return None
    has_email_or_text_input = re.search(
        r'<input\b[^>]*type=["\'](?:text|email|tel)["\']',
        pg_html, re.IGNORECASE,
    )
    if not has_email_or_text_input:
        return None
    if SPAM_PROTECTION_RE.search(pg_html):
        return None
    if HONEYPOT_FIELD_RE.search(pg_html):
        return None
    return ScanFinding(
        category="security",
        severity="low",
        title="Contact form lacks spam protection — no reCAPTCHA, hCaptcha, or honeypot detected",
        description=(
            "This page contains a contact/inquiry form with no detectable spam protection. "
            "Without bot protection, spambots can submit hundreds of fake entries per day — "
            "flooding your inbox, injecting malicious URLs into auto-reply emails, and "
            "potentially triggering spam flags on your email domain if bounce patterns "
            "are misidentified as outbound spam. Most small business contact forms are "
            "discovered and targeted by spam bots within 2–4 weeks of launch."
        ),
        remediation=(
            "Add a spam protection mechanism to your contact form. "
            "Option 1 (easiest): In WordPress, install WPForms or Contact Form 7 + reCAPTCHA addon — "
            "both have free reCAPTCHA v3 integration (invisible, no user friction). "
            "Option 2: Add Cloudflare Turnstile (free) — replace your submit button area with "
            "<div class='cf-turnstile' data-sitekey='YOUR_KEY'></div>. "
            "Option 3 (no external service): Add a honeypot hidden field that bots fill but "
            "humans leave blank: <input type='text' name='website' style='display:none' tabindex='-1'>. "
            "Verify by watching your inbox after deployment — spam should drop within 24–48 hours."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet="<form> with text/email input — no reCAPTCHA, hCaptcha, Turnstile, or honeypot detected",
            metadata={"owasp": "A07:2021", "spam_risk": "high"},
        ),
        confidence=0.61,
    )


def _check_multiple_font_families(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect pages loading 3+ distinct Google Font families — font loading waterfall (v35).

    Each distinct Google Fonts family requires a separate network round-trip to discover
    the font file URLs. With 3+ families, the browser must make 3+ DNS lookups,
    3+ HTTP requests, and wait for 3+ font file downloads before the page can render
    styled text. This creates a render-blocking 'font waterfall' that can add 300–1200ms
    to First Contentful Paint on average connections.
    """
    family_matches = GOOGLE_FONT_FAMILY_RE.findall(pg_html)
    if not family_matches:
        return None
    # Count distinct families across all Google Fonts links
    unique_families: set[str] = set()
    for match in family_matches:
        # Each match may be "Roboto|Open+Sans|Lato" or "Roboto,Open+Sans"
        parts = re.split(r'[|,]', match)
        for part in parts:
            # Normalize: URL decode + and %20, strip variant suffixes like :ital,wght@0,400
            family = re.sub(r'(?::[^,|]+)?$', '', part)
            family = family.replace('+', ' ').replace('%20', ' ').strip()
            if family:
                unique_families.add(family.lower())
    family_count = len(unique_families)
    if family_count < 3:
        return None
    severity = "medium" if family_count >= 5 else "low"
    families_list = sorted(unique_families)[:5]
    return ScanFinding(
        category="performance",
        severity=severity,
        title=f"{family_count} Google Font families loaded — font waterfall delays text render",
        description=(
            f"Your site loads {family_count} separate Google Font families "
            f"({', '.join(families_list[:3])}{'...' if len(families_list) > 3 else ''}). "
            "Each font family requires its own network round-trip: a CSS stylesheet request, "
            "then 1–4 font file downloads per weight/style variant. "
            f"With {family_count} families, browsers must complete {family_count} separate "
            "font download chains before text can render in the correct typeface. "
            "On a typical 4G connection, each additional font family adds ~150–300ms to "
            "Time to First Meaningful Paint — a compounding performance penalty."
        ),
        remediation=(
            f"Consolidate from {family_count} font families to 2 or fewer. "
            "Step 1: Audit your design — identify which font families are actually visible on the page. "
            "Step 2: Pick one font for headings and one for body text (two total). "
            "Step 3: Remove unused family imports from your Google Fonts URL or stylesheet. "
            "In WordPress with Elementor: Appearance > Theme Style > Typography allows you to "
            "remove extra font families in minutes without code. "
            "Alternative: Self-host fonts using google-webfonts-helper (free tool) to serve "
            "them from your own server with optimal caching. "
            "Validate the reduction with PageSpeed Insights: look for 'Reduce unused CSS'."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=f"Google Fonts families detected: {', '.join(sorted(unique_families)[:5])}",
            metadata={"font_family_count": family_count, "families": list(sorted(unique_families))[:8]},
        ),
        confidence=0.76,
    )


def _check_tracking_pixel_overload(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect pages loading 4+ third-party marketing tracking scripts (v36).

    Beyond GA4, many SMB sites accumulate marketing pixels: Facebook Pixel, Hotjar,
    Microsoft Clarity, Mixpanel, Segment, FullStory, LinkedIn Insight Tag, Twitter Pixel,
    Criteo, HubSpot, Intercom, and Drift. Each script requires a separate DNS lookup,
    TCP connection, and script download before the page can finish loading. At 4+ scripts
    the cumulative delay reaches 400–1200ms on average mobile connections, adding
    measurable latency to Core Web Vitals (LCP, FID, TBT). Additionally, each pixel
    independently sets cookies and sends user behaviour data to a different vendor —
    raising GDPR/CCPA compliance surface area for site owners unaware of the implications.
    """
    matches = TRACKING_PIXEL_RE.findall(pg_html)
    pixel_count = len(matches)
    if pixel_count < 4:
        return None
    severity = "medium" if pixel_count >= 6 else "low"
    return ScanFinding(
        category="performance",
        severity=severity,
        title=f"{pixel_count} third-party marketing pixels detected — compounding load latency",
        description=(
            f"Your site loads {pixel_count} separate third-party tracking/analytics scripts "
            "(e.g., Facebook Pixel, Hotjar, Clarity, HubSpot, LinkedIn Insight Tag). "
            "Each script forces an extra DNS lookup and network round-trip before the page "
            "can finish loading. At this volume, cumulative tracking overhead can add "
            "400–1,200ms to First Input Delay and Largest Contentful Paint on mobile "
            "connections — directly impacting Google's Core Web Vitals score and user "
            "bounce rates. Every 100ms of added latency reduces conversions by ~1%."
        ),
        remediation=(
            f"Audit your active tracking scripts and remove any you no longer actively use. "
            "Step 1: In Google Tag Manager (or your theme settings), identify which pixels "
            "are firing on every page vs. only on conversion events. "
            "Step 2: Move Facebook Pixel and HubSpot to fire on thank-you/confirmation pages "
            "only — not on every page load. "
            "Step 3: Replace Hotjar + Clarity with just one session recording tool. "
            "Step 4: Consolidate via GTM with lazy-loading so pixels fire after user "
            "interaction rather than on initial page load. "
            "Google PageSpeed Insights 'Reduce the impact of third-party code' will validate "
            "the improvement after each removal."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=f"{pixel_count} tracking pixel scripts detected on page",
            metadata={"pixel_count": pixel_count},
        ),
        confidence=0.81,
    )


def _check_html_email_exposure(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect raw email addresses in page HTML not inside mailto: links (v36).

    Plaintext email addresses in HTML source are trivially harvested by spam bots
    within days of page indexing. Most web crawlers are purpose-built to extract
    emails from public HTML for use in spam campaigns, phishing, and credential
    stuffing attacks. A single exposed business email can receive hundreds of spam
    messages daily, degrading email deliverability and potentially causing the
    sending domain to be blocklisted. Addresses wrapped in mailto: links are still
    harvestable but slightly more friction — truly exposed plaintext addresses
    (outside any href attribute) are the highest-risk pattern.
    """
    # Find all email-like strings in page
    all_emails = set(EMAIL_IN_BODY_RE.findall(pg_html))
    if not all_emails:
        return None
    # Extract emails already inside mailto: links — those are somewhat expected
    mailto_emails: set[str] = set()
    for match in EMAIL_IN_MAILTO_RE.finditer(pg_html):
        mailto_emails.update(EMAIL_IN_BODY_RE.findall(match.group(0)))
    # Look for emails appearing as raw text (in tag content, not in href=)
    # Heuristic: remove href="...email..." contexts and look for remaining occurrences
    stripped = re.sub(r'href=["\'][^"\']+["\']', '', pg_html, flags=re.IGNORECASE)
    exposed_emails = set(EMAIL_IN_BODY_RE.findall(stripped))
    if not exposed_emails:
        return None
    # Filter out clearly structural/placeholder addresses (e.g. example.com, test domain)
    structural = re.compile(
        r'@(?:example\.com|example\.org|test\.com|yourdomain\.com|domain\.com|email\.com)'
        r'|^(?:no-reply|noreply|nobody|user|name)@'
        r'|^(?:user@domain\.com|name@email\.com)$',
        re.IGNORECASE,
    )
    real_exposed = [e for e in exposed_emails if not structural.search(e)]
    if not real_exposed:
        return None
    sample = sorted(real_exposed)[:2]
    return ScanFinding(
        category="security",
        severity="low",
        title=f"Raw email address exposed in page HTML — spam harvesting risk",
        description=(
            f"{'An email address' if len(real_exposed) == 1 else f'{len(real_exposed)} email addresses'} "
            f"({'e.g. ' + sample[0]}) {'is' if len(real_exposed) == 1 else 'are'} "
            "visible as plaintext in your page's HTML source. Spam bots automatically crawl "
            "web pages and extract email addresses within hours of indexing. "
            "Exposed business emails commonly receive 50–500 spam messages daily, and are "
            "frequently used in phishing campaigns impersonating your brand. "
            "Email exposure can also lead to deliverability issues if spam volumes cause "
            "your domain to appear on email blocklists."
        ),
        remediation=(
            "Replace plaintext email addresses with a contact form or use email obfuscation. "
            "Option 1 (best): Remove the plaintext email entirely and redirect to a contact form. "
            "Option 2: Use CSS/JS obfuscation — display the address visually but don't include "
            "the full string in HTML source. In WordPress: use a plugin like 'Email Encoder Bundle' "
            "(free) which automatically obfuscates all emails on the site with zero configuration. "
            "Option 3: Use a human-readable format with character substitution: "
            "'info [at] yourdomain [dot] com' — bots don't reliably parse this pattern. "
            "Validate with 'view source' after implementation — no recognizable email format "
            "should appear in the raw HTML."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=f"Exposed email(s): {', '.join(sample)}",
            metadata={"exposed_count": len(real_exposed), "examples": sample},
        ),
        confidence=0.78,
    )


def _check_missing_organization_schema(pg_html: str, page_url: str, root_url: str) -> "ScanFinding | None":
    """Detect homepage without Organization JSON-LD schema — Knowledge Panel gap (v36).

    Organization schema tells Google the authoritative name, logo, URL, social profiles,
    and contact information for a business entity. Without it, Google relies on heuristics
    to build its Knowledge Panel and may display incorrect information, merge your business
    with a similarly-named competitor, or display no Knowledge Panel at all. Organization
    schema is separate from LocalBusiness (which covers physical location signals) and
    WebSite (which enables Sitelinks Searchbox). All three are complementary and should
    coexist on the homepage for maximum Entity Authority in Google's Knowledge Graph.
    """
    if page_url.rstrip("/") != root_url.rstrip("/"):
        return None  # Only check homepage
    # Already has Organization schema?
    if ORGANIZATION_SCHEMA_RE.search(pg_html):
        return None
    # Also skip if LocalBusiness schema is present (it extends Organization — close enough)
    if LOCAL_BUSINESS_SCHEMA_RE.search(pg_html):
        return None
    # Only flag if the page has some structured data context (has LD+JSON at all)
    # to avoid flooding minimal pages that have no schema at all (separate finding)
    # Actually flag regardless — Organization schema gap is meaningful on any business site
    return ScanFinding(
        category="seo",
        severity="low",
        title="Homepage missing Organization JSON-LD schema — Google Knowledge Panel gap",
        description=(
            "Your homepage has no Organization structured data (JSON-LD). "
            "Google uses Organization schema to build its Knowledge Panel — the branded "
            "information box that appears in search results when someone searches for your "
            "business by name. Without it, Google may display inaccurate business information, "
            "fail to connect your website to your Google Business Profile, or show a competitor "
            "in the Knowledge Panel slot. Organization schema also enables logo display in "
            "Google's index and helps establish your business as a distinct named entity, "
            "which improves brand query rankings and brand-name SERP real estate."
        ),
        remediation=(
            'Add a <script type="application/ld+json"> block to your homepage <head> section: '
            '{"@context": "https://schema.org", "@type": "Organization", '
            '"name": "Your Business Name", "url": "https://yourdomain.com", '
            '"logo": "https://yourdomain.com/logo.png", '
            '"sameAs": ["https://www.facebook.com/yourbusiness", '
            '"https://www.linkedin.com/company/yourbusiness"]}. '
            "In WordPress: Yoast SEO (free) automatically generates Organization schema "
            "under SEO → Search Appearance → General. "
            "Validate using Google's Rich Results Test at search.google.com/test/rich-results "
            "after adding."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet="No Organization or LocalBusiness JSON-LD found on homepage",
            metadata={"wcag_ref": "N/A", "google_ref": "Organization schema — Google Search Central"},
        ),
        confidence=0.73,
    )


def _check_image_lazy_loading_coverage(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Detect pages with 6+ images where <30% use loading='lazy' (v36).

    HTML native lazy loading (loading='lazy') defers off-screen images until the user
    scrolls near them. On image-heavy pages, eager-loading all images on initial page
    load forces the browser to download assets that may never be viewed, wasting
    bandwidth and delaying the loading of above-the-fold content. For pages with 6+
    images, lazy loading the below-the-fold images typically reduces initial payload
    by 200–800KB and improves Largest Contentful Paint (LCP) by 300–800ms on mobile.
    """
    all_imgs = IMG_TAG_RE.findall(pg_html)
    img_count = len(all_imgs)
    if img_count < 6:
        return None
    lazy_count = len(LAZY_LOAD_RE.findall(pg_html))
    if img_count == 0:
        return None
    lazy_ratio = lazy_count / img_count
    if lazy_ratio >= 0.30:
        return None
    eager_count = img_count - lazy_count
    return ScanFinding(
        category="performance",
        severity="low",
        title=f"Image lazy loading not applied — {eager_count}/{img_count} images load eagerly",
        description=(
            f"This page contains {img_count} images but only {lazy_count} use the HTML "
            "`loading='lazy'` attribute. The remaining {eager_count} images are eagerly "
            "loaded on initial page request, including those below the fold that most "
            "visitors may never scroll to. On image-heavy pages, this pattern wastes "
            "bandwidth, delays Time to First Byte for meaningful content, and increases "
            "Largest Contentful Paint (LCP) — a Core Web Vitals metric Google uses directly "
            "in ranking. On a 4G mobile connection, 10 unoptimized images can add "
            "800ms–2s to perceived load time."
        ),
        remediation=(
            f"Add loading='lazy' to all images except your hero/above-the-fold image. "
            "The hero image (first visible image) should NOT be lazy-loaded — it is your "
            "LCP candidate and must load as fast as possible. "
            "In WordPress: install the 'Smush' or 'ShortPixel' plugin (both free tiers) — "
            "they automatically add lazy loading to all eligible images site-wide with one click. "
            "For custom HTML: simply add the attribute to each <img> tag: "
            "<img src='photo.jpg' loading='lazy' alt='...'>. "
            "Browser support is now 96%+ globally — no JavaScript or polyfill needed. "
            "Validate with PageSpeed Insights: look for 'Defer offscreen images' in Opportunities."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=f"{lazy_count} of {img_count} images have loading='lazy' ({int(lazy_ratio*100)}% coverage)",
            metadata={"total_images": img_count, "lazy_images": lazy_count, "eager_images": eager_count},
        ),
        confidence=0.77,
    )


def _check_robots_sitemap_directive(robots_raw: str, base_url: str) -> "ScanFinding | None":
    """Detect robots.txt without a Sitemap: directive for auto-discovery (v36).

    While Google can find sitemaps via Google Search Console submission, the Sitemap:
    directive in robots.txt is the most reliable passive discovery mechanism. All major
    search engine crawlers (Googlebot, Bingbot, DuckDuckGo, Apple) read robots.txt on
    every crawl cycle and automatically queue any declared sitemap URLs for processing.
    Without this directive, newly published pages and content updates may take days or
    weeks longer to be discovered, particularly for sites that don't have many inbound
    links. This is especially important for service businesses that publish seasonal
    offers, blog content, or location pages.
    """
    if not robots_raw.strip():
        return None  # No robots.txt fetched — separate finding handles that
    if ROBOTS_SITEMAP_DIRECTIVE_RE.search(robots_raw):
        return None  # Sitemap directive already present
    return ScanFinding(
        category="seo",
        severity="low",
        title="robots.txt missing Sitemap: directive — slower search engine discovery",
        description=(
            "Your robots.txt file does not include a Sitemap: directive pointing to your "
            "XML sitemap. Search engine crawlers (Googlebot, Bingbot) read robots.txt on "
            "every crawl visit and automatically queue any declared sitemaps for processing. "
            "Without this directive, new pages and content updates may take days or weeks "
            "longer to be indexed, as crawlers must discover URLs through links alone. "
            "This is a widely documented best practice in Google's official robots.txt "
            "specification and is a zero-effort improvement that costs nothing to implement."
        ),
        remediation=(
            "Add the following line to the bottom of your robots.txt file: "
            f"Sitemap: {base_url.rstrip('/')}/sitemap.xml "
            "(adjust the path if your sitemap is at a different URL — check /sitemap_index.xml "
            "or /sitemap.xml to confirm the correct location). "
            "In WordPress: Yoast SEO and Rank Math automatically maintain robots.txt via "
            "SEO → Tools → File Editor. "
            "You can also submit your sitemap directly in Google Search Console under "
            "Sitemaps — both approaches complement each other. "
            "Validate with: https://www.google.com/search?q=site:yourdomain.com to check "
            "indexation coverage after the next Googlebot crawl (typically 1–7 days)."
        ),
        evidence=WebsiteEvidence(
            page_url=base_url,
            snippet="robots.txt present but no Sitemap: directive found",
            metadata={"robots_line_count": len(robots_raw.strip().splitlines())},
        ),
        confidence=0.82,
    )


def _check_pagination_rel_links(pg_html: str, page_url: str, root_url: str) -> "ScanFinding | None":
    """SEO/low: paginated inner page missing rel=prev/next link elements (v37).

    Google uses rel=prev/next pagination signals to understand multi-page content
    relationships (blog archives, product listings, paginated articles) and consolidate
    authority across the series. Without these link elements, each archive page may be
    treated as an independent page with thin content, diluting the authority of the
    primary archive URL and making it harder for Googlebot to efficiently crawl all
    paginated content. Fires on inner pages only where URL structure indicates pagination.
    """
    if page_url == root_url:
        return None
    url_path = urlparse(page_url).path.lower()
    url_query = urlparse(page_url).query.lower()
    is_paginated = (
        "/page/" in url_path
        or url_path.rstrip("/").rsplit("/", 1)[-1].isdigit()
        or "page=" in url_query
    )
    if not is_paginated:
        return None
    if PAGINATION_REL_RE.search(pg_html):
        return None
    return ScanFinding(
        category="seo",
        severity="low",
        title="Paginated page missing rel=prev/next link elements",
        description=(
            f"The page at {page_url} appears to be part of a paginated series "
            "(e.g., blog archive or product listing) but does not include "
            "<link rel='prev'> or <link rel='next'> link elements in the <head>. "
            "Google uses pagination signals to understand multi-page content relationships "
            "and may treat each archive page as independent, diluting the authority of your "
            "main archive page and slowing Googlebot's crawl of your full content catalog."
        ),
        remediation=(
            "Add <link rel='prev' href='...'> and <link rel='next' href='...'> tags to the "
            "<head> of each paginated archive page. Most CMS platforms (WordPress, WooCommerce, "
            "Shopify) support this natively — check that an SEO plugin has not suppressed them. "
            "In WordPress with Yoast SEO, verify 'Noindex' is not enabled on archive/category pages. "
            "Validate with Google's Rich Results Test after adding the tags."
        ),
        evidence=WebsiteEvidence(page_url=page_url, metadata={"paginated_url": page_url}),
        confidence=0.71,
    )


def _check_missing_article_schema(pg_html: str, page_url: str, root_url: str) -> "ScanFinding | None":
    """SEO/low: blog/news inner page with article content but no Article/BlogPosting JSON-LD (v37).

    Article structured data enables Google to display enhanced search results for content
    pages: author name, publication date, and article rich results (headline, image, date
    in SERP snippets). Without Article schema on blog or news pages, the content misses
    eligibility for Google News and Top Stories carousels, and click-through rates are
    reduced compared to schema-annotated competitors. Only fires for pages with a blog/news
    URL path and at least 150 words of content to avoid false positives on stub pages.
    """
    if page_url == root_url:
        return None
    url_path = urlparse(page_url).path.lower()
    is_article_page = (
        "/blog/" in url_path
        or "/news/" in url_path
        or "/post/" in url_path
        or "/article/" in url_path
    )
    if not is_article_page:
        return None
    words = WORD_CONTENT_RE.findall(pg_html)
    if len(words) < 150:
        return None
    if ARTICLE_SCHEMA_RE.search(pg_html):
        return None
    return ScanFinding(
        category="seo",
        severity="low",
        title="Blog/news page missing Article structured data",
        description=(
            f"The page at {page_url} appears to be a blog or news article but has no "
            "Article, BlogPosting, or NewsArticle JSON-LD schema markup. "
            "Without Article schema, Google cannot display author bylines, publish dates, "
            "and article rich results in search, reducing organic click-through rates on "
            "content-focused pages and missing eligibility for Google News indexing."
        ),
        remediation=(
            "Add a BlogPosting JSON-LD block to each blog post's <head>. "
            'Include: @type (BlogPosting), "headline", "author" (Person schema with name), '
            '"datePublished", "dateModified", and "image". '
            "In WordPress, Yoast SEO or Rank Math generates this automatically when "
            "Article type is selected in the post settings — takes under 5 minutes to enable "
            "site-wide. Validate with Google's Rich Results Test (search.google.com/rich-results-test)."
        ),
        evidence=WebsiteEvidence(page_url=page_url, metadata={"article_path": url_path}),
        confidence=0.68,
    )


def _check_footer_contact_missing(pg_html: str, page_url: str, root_url: str) -> "ScanFinding | None":
    """Conversion/low: homepage without phone/email/address in the footer area (v37).

    Visitors who scroll to the bottom of a page are actively looking for contact information.
    Failure to provide a phone number, email address, or physical address in the footer creates
    friction at the decision point and reduces lead generation for service businesses. A visible
    footer address also reinforces local SEO business location signals by giving search engines
    a consistent NAP (Name/Address/Phone) citation on every page. Fires on homepage only to
    avoid noise from inner pages that legitimately delegate contact info to the footer layout.
    """
    if page_url != root_url:
        return None
    # Try to isolate the footer area — check after <footer> tag or last 2500 chars
    footer_match = FOOTER_SECTION_RE.search(pg_html)
    if footer_match:
        footer_html = pg_html[footer_match.start():]
    else:
        footer_html = pg_html[-2500:]
    has_phone_in_footer = bool(PHONE_RE.search(footer_html))
    has_email_in_footer = bool(
        re.search(r'\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b', footer_html)
    )
    has_address_in_footer = bool(ADDRESS_TEXT_RE.search(footer_html))
    if has_phone_in_footer or has_email_in_footer or has_address_in_footer:
        return None
    # A footer contact page link is a partial signal — skip if it exists
    if CONTACT_LINK_RE.search(footer_html):
        return None
    return ScanFinding(
        category="conversion",
        severity="low",
        title="No contact information in page footer",
        description=(
            "The homepage footer does not appear to include a phone number, email address, "
            "or physical address. Visitors who scroll to the footer are actively seeking "
            "contact options — failure to provide them creates friction at the decision point "
            "and reduces lead conversion for service businesses. A visible footer address also "
            "supports local SEO by reinforcing business location signals on every page."
        ),
        remediation=(
            "Add your business phone number (wrapped in a tel: link for mobile click-to-call), "
            "email address, and physical address to the footer. Most page builders (Elementor, "
            "Squarespace, Wix) have a footer info widget — this takes under 30 minutes and "
            "simultaneously improves conversion and local SEO. "
            "Wrap the address in PostalAddress schema markup for structured data benefit: "
            '{"@type": "PostalAddress", "streetAddress": "...", "addressLocality": "...", '
            '"addressRegion": "...", "postalCode": "..."}.'
        ),
        evidence=WebsiteEvidence(page_url=page_url),
        confidence=0.73,
    )


def _check_broken_anchor_links(pg_html: str, page_url: str) -> "ScanFinding | None":
    """SEO/low: page has href='#fragment' links where the target id doesn't exist (v37).

    Anchor links that reference on-page section IDs which don't exist either silently
    fail (the page stays in place) or scroll to the top, creating a confusing dead end
    for visitors mid-page. From a search engine perspective, broken in-page navigation
    signals poor page maintenance and content quality. Fires when ≥2 distinct broken
    anchors are found to reduce false positives from template fragments.
    """
    fragment_hrefs = ANCHOR_HREF_FRAGMENT_RE.findall(pg_html)
    if len(fragment_hrefs) < 2:
        return None
    existing_ids = set(INPUT_ID_RE.findall(pg_html))
    broken = [
        f"#{frag}" for frag in fragment_hrefs
        if frag not in existing_ids and frag not in ("top", "")
    ]
    if len(broken) < 2:
        return None
    snippet = ", ".join(broken[:4]) + ("..." if len(broken) > 4 else "")
    return ScanFinding(
        category="seo",
        severity="low",
        title="Broken anchor links pointing to missing page sections",
        description=(
            f"This page contains {len(broken)} anchor link(s) (e.g., {snippet}) "
            "that reference on-page section IDs which do not exist. "
            "When visitors or bots click these, they either scroll to the top (confusing UX) "
            "or silently fail. Broken in-page navigation is a crawlability and page quality "
            "signal evaluated by search engines."
        ),
        remediation=(
            "Audit all href='#...' anchor links on this page. For each anchor, ensure a matching "
            "id='...' attribute exists on the target element. "
            "In WordPress, check that anchor-linked headings have the Heading Block anchor field filled. "
            "Use browser Dev Tools → Elements panel (Ctrl+F) to search for missing IDs quickly. "
            "Takes under 30 minutes to audit and fix on a typical service page."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=snippet[:120],
            metadata={"broken_anchor_count": len(broken), "examples": broken[:4]},
        ),
        confidence=0.74,
    )


def _check_duplicate_script_tags(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Performance/low: same external script src referenced more than once on a page (v37).

    Duplicate script tags occur when plugins, theme components, or page builder widgets
    each independently load the same library (most commonly jQuery, analytics snippets,
    or third-party widgets). This doubles the network request cost for that script,
    potentially executes it twice (causing analytics double-counting or JavaScript errors),
    and adds unnecessary render-blocking overhead. Fires when any script src appears ≥2 times
    (after URL normalisation) to catch both exact duplicates and version-identical loads.
    """
    all_srcs = DUPLICATE_SCRIPT_RE.findall(pg_html)
    if len(all_srcs) < 2:
        return None
    counts: dict[str, int] = {}
    for src in all_srcs:
        # Normalise: lowercase, strip query string, strip trailing slash
        norm = src.lower().split("?")[0].rstrip("/")
        counts[norm] = counts.get(norm, 0) + 1
    dupes = [src for src, cnt in counts.items() if cnt >= 2]
    if not dupes:
        return None
    snippet = dupes[0].rsplit("/", 1)[-1][:80]
    return ScanFinding(
        category="performance",
        severity="low",
        title="Duplicate external script tags detected",
        description=(
            f"The same external script is loaded {counts[dupes[0]]} times on this page "
            f"(e.g., '{snippet}'). Duplicate script tags double the network request overhead, "
            "may execute the script twice (causing analytics double-counting, race conditions, "
            "or JavaScript errors), and add unnecessary render-blocking delay on every page load."
        ),
        remediation=(
            "Remove duplicate <script src='...'> tags from the page template or theme. "
            "In WordPress: check that no two plugins are loading the same library (jQuery, "
            "analytics, slider, etc.). Use Dev Tools → Network tab, filter by JS, and look "
            "for repeated filenames. Most page builders have a 'dequeue scripts' or script "
            "manager option to prevent conflicting loads — this fix takes under 30 minutes."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=snippet,
            metadata={"duplicate_script": dupes[0], "load_count": counts[dupes[0]]},
        ),
        confidence=0.88,
    )


def _check_image_alt_short_text(pg_html: str, page_url: str) -> "ScanFinding | None":
    """ADA/low-medium: images with alt text ≤2 chars that are meaningless to screen readers (v38).

    Alt text like alt="-", alt=".", alt="*", alt="1", or a single space is technically
    present but carries no semantic value for screen reader users. These are often the
    result of CMS auto-generation, template placeholders, or developer laziness. Unlike
    alt="" (which signals a decorative image), these short strings are interpreted as
    meaningful text and read aloud literally — e.g. "dash" or "asterisk" — creating a
    confusing and non-compliant experience. Fires when ≥2 such images are found; severity
    escalates to medium at ≥4.
    """
    matches = ALT_SHORT_TEXT_RE.findall(pg_html)
    # Keep only truly meaningless values (≤2 chars, non-empty, and not 2-letter meaningful codes)
    _MEANINGFUL_SHORT = re.compile(r'^[a-zA-Z]{2}$')  # 2-letter codes like "en" or "OK" are meaningful
    bad = [
        m for m in matches
        if 1 <= len(m.strip()) <= 2 and not _MEANINGFUL_SHORT.match(m.strip())
    ]
    if len(bad) < 2:
        return None
    severity = "medium" if len(bad) >= 4 else "low"
    snippet = f'alt="{bad[0]}"'
    return ScanFinding(
        category="ada",
        severity=severity,
        title="Images with meaningless alt text (≤2 character alt values)",
        description=(
            f"Found {len(bad)} image(s) on this page with alt text that is ≤2 characters "
            f"(e.g., {snippet}). While technically present, these alt values are meaningless "
            "to screen reader users — they are read aloud literally as symbols (e.g., 'dash', 'period') "
            "rather than describing the image. This violates WCAG 2.1 SC 1.1.1 Non-text Content "
            "and creates a confusing and non-compliant experience for visually impaired visitors."
        ),
        remediation=(
            "Replace each short/placeholder alt attribute with a genuine description of the image "
            "(e.g., alt='Technician servicing HVAC unit on rooftop'). For purely decorative images "
            "that convey no information, use an empty alt attribute (alt=\"\") — this tells screen "
            "readers to skip the image entirely. In WordPress: edit each image block and update the "
            "'Alternative Text' field in the block sidebar."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=snippet,
            metadata={"meaningless_alt_count": len(bad), "example_value": bad[0]},
        ),
        confidence=0.77,
    )


def _check_heading_keyword_stuffing(pg_html: str, page_url: str) -> "ScanFinding | None":
    """SEO/low: heading tags containing 3+ pipe- or comma-separated keyword phrases (v38).

    Keyword stuffing in H1/H2 tags — e.g.
    <h1>Plumber | Plumber Near Me | Emergency Plumber NYC | Cheap Plumber Brooklyn</h1>
    is explicitly covered by Google's spam policies. While this was a common black-hat
    SEO tactic in the early 2010s, modern Google algorithms treat it as a negative
    ranking signal. It also creates a terrible user experience, undermining the
    professional credibility of the page.
    """
    h1_texts = H1_CONTENT_RE.findall(pg_html)
    h2_texts = H2_CONTENT_RE.findall(pg_html)
    stuffed: list[str] = []
    for raw in h1_texts + h2_texts:
        clean = re.sub(r'<[^>]+>', '', raw).strip()
        if HEADING_KEYWORD_STUFF_RE.search(clean):
            stuffed.append(clean[:120])
    if not stuffed:
        return None
    snippet = stuffed[0]
    return ScanFinding(
        category="seo",
        severity="low",
        title="Keyword-stuffed heading tag detected",
        description=(
            "A heading tag (H1 or H2) contains what appears to be a pipe- or comma-separated "
            "list of keyword phrases rather than a natural, readable heading. For example: "
            f"'{snippet[:80]}'. Google's spam policies explicitly flag keyword stuffing in "
            "headings as a quality issue that can suppress rankings. It also reduces user trust "
            "and signals to visitors that the page prioritises search bots over human readers."
        ),
        remediation=(
            "Rewrite the heading to a single, clear, natural-language phrase that describes "
            "the page content for a human reader (e.g., 'Emergency Plumbing Services in NYC — "
            "Available 24/7'). Use the remaining keyword phrases as sub-headings (H2s), in "
            "body copy, or in your meta title tag instead. In WordPress with Yoast or Rank Math, "
            "the heading editor highlights keyword overuse — use the readability analysis to guide rewrites."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=snippet[:200],
            metadata={"stuffed_heading_count": len(stuffed)},
        ),
        confidence=0.79,
    )


def _check_analytics_preconnect_missing(pg_html: str, page_url: str, root_url: str) -> "ScanFinding | None":
    """Performance/low: GA4/UA analytics loaded without preconnect/dns-prefetch hint (v38).

    Google Analytics (GA4 or Universal Analytics) requires a DNS lookup and TLS handshake
    to google-analytics.com and googletagmanager.com on every page load. Without a
    preconnect or dns-prefetch hint, this lookup is deferred until the analytics script
    is encountered mid-parse — adding 100–300ms of latency for first-time visitors.
    Adding a preconnect hint in the <head> instructs the browser to resolve and warm the
    connection before the script tag is parsed, eliminating this hidden load-time tax.
    Fires only on root URL to avoid duplicate findings across crawled pages.
    """
    if urlparse(page_url).path.rstrip("/") not in ("", "/") or page_url != root_url and "/" in urlparse(page_url).path.lstrip("/"):
        return None
    has_analytics = bool(GA_TRACKING_ID_RE.search(pg_html) or ANALYTICS_RE.search(pg_html))
    if not has_analytics:
        return None
    has_hint = bool(ANALYTICS_PRECONNECT_HINT_RE.search(pg_html))
    if has_hint:
        return None
    return ScanFinding(
        category="performance",
        severity="low",
        title="Google Analytics loaded without preconnect resource hint",
        description=(
            "Google Analytics is detected on this page but there is no <link rel='preconnect'> "
            "or <link rel='dns-prefetch'> hint for google-analytics.com or googletagmanager.com "
            "in the <head>. Without a preconnect hint, the browser must complete a full DNS lookup "
            "+ TLS handshake for analytics domains during page parsing — adding an estimated "
            "100–300ms of latency on cold loads. This contributes to First Contentful Paint (FCP) "
            "and Largest Contentful Paint (LCP) delays that affect Google's Core Web Vitals score."
        ),
        remediation=(
            "Add the following two lines inside your <head> tag, before any script tags:\n"
            "<link rel='preconnect' href='https://www.google-analytics.com'>\n"
            "<link rel='preconnect' href='https://www.googletagmanager.com'>\n"
            "In WordPress: add via your theme's functions.php using wp_resource_hints(), "
            "or via a header snippet plugin like Insert Headers and Footers. "
            "This takes under 5 minutes and can shave 150–250ms from FCP on cold visits."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet="analytics loaded; no preconnect hint found",
            metadata={"analytics_detected": True, "preconnect_hint_present": False},
        ),
        confidence=0.74,
    )


def _check_form_error_handling_absent(pg_html: str, page_url: str) -> "ScanFinding | None":
    """ADA/low: form with required fields but no ARIA live region for error messages (v38).

    When a form submission fails validation, screen reader users depend on ARIA live regions
    (role='alert' or aria-live='assertive/polite') or aria-errormessage attributes to be
    notified of the error. Without these, a screen reader user submits the form, nothing
    happens visually (from their perspective), and there is no accessible notification
    explaining which fields failed validation — leaving them unable to correct and resubmit.
    This violates WCAG 2.1 SC 3.3.1 (Error Identification) and SC 4.1.3 (Status Messages).
    Fires when required fields are present but no accessible error announcement mechanism exists.
    """
    has_form = bool(FORM_RE.search(pg_html))
    if not has_form:
        return None
    required_fields = REQUIRED_FIELD_RE.findall(pg_html)
    if len(required_fields) < 1:
        return None
    has_aria_live = bool(ARIA_LIVE_RE.search(pg_html))
    if has_aria_live:
        return None
    return ScanFinding(
        category="ada",
        severity="low",
        title="Form required fields lack accessible error announcement (WCAG 3.3.1)",
        description=(
            f"This page contains a form with {len(required_fields)} required field(s) "
            "but no ARIA live region (role='alert', aria-live='assertive', or "
            "aria-errormessage) is present for error messages. Screen reader users who "
            "fail form validation will not receive an accessible notification of the error — "
            "they submit the form, nothing visible happens, and they receive no feedback on "
            "what to fix. This violates WCAG 2.1 SC 3.3.1 Error Identification and "
            "SC 4.1.3 Status Messages, exposing the site to ADA demand letter risk."
        ),
        remediation=(
            "Add role='alert' to your form error message container so screen readers announce "
            "errors immediately upon validation failure:\n"
            "<div role='alert' id='form-errors' aria-live='assertive'></div>\n"
            "Populate it dynamically with error text when the form is submitted. "
            "In WordPress contact form plugins (Contact Form 7, WPForms, Gravity Forms): "
            "most modern versions include ARIA error support by default — ensure you are "
            "running the latest plugin version. For custom forms, a developer can add this "
            "in under 30 minutes."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=f"required fields: {len(required_fields)}; aria-live: absent",
            metadata={"required_field_count": len(required_fields), "aria_live_present": False},
        ),
        confidence=0.72,
    )


def _check_charset_declaration_missing(pg_html: str, page_url: str) -> "ScanFinding | None":
    """SEO/low: HTML page without a <meta charset> declaration in the head (v38).

    A missing charset declaration causes browsers to use fallback character encoding
    detection, which can produce 'mojibake' (garbled text) for pages with non-ASCII
    characters (accented names, currency symbols, em-dashes, etc.). Google's crawlers
    also rely on charset declarations to correctly parse and index page content — an
    absent declaration can cause indexing errors for pages with extended character sets.
    Additionally, HTML validators and SEO audit tools flag this as a basic technical
    hygiene issue. The fix is a single-line HTML change.
    """
    head_match = HEAD_SECTION_RE.search(pg_html)
    if not head_match:
        return None
    head_html = head_match.group(1)
    if META_CHARSET_RE.search(head_html):
        return None
    return ScanFinding(
        category="seo",
        severity="low",
        title="HTML page missing charset declaration",
        description=(
            "This page's <head> section does not contain a <meta charset> declaration. "
            "Without a charset declaration, browsers use heuristic encoding detection — "
            "which can render extended characters (accented letters, currency symbols, "
            "em-dashes) as garbled text ('mojibake'). Google's crawlers rely on charset "
            "declarations for correct content indexing. This is a basic HTML hygiene issue "
            "flagged by all major SEO audit tools and HTML validators."
        ),
        remediation=(
            "Add the following as the very first tag inside your <head> element (before any "
            "other meta tags or scripts):\n"
            "<meta charset='UTF-8'>\n"
            "UTF-8 is the correct value for virtually all modern websites. In WordPress, "
            "this is typically included by the theme's header.php automatically — if it's "
            "missing, check whether a custom or third-party theme has removed it. "
            "This is a one-line fix that takes under 5 minutes."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet="<head> — no meta charset found",
            metadata={"charset_present": False},
        ),
        confidence=0.85,
    )


def _check_skip_nav_link(pg_html: str, page_url: str) -> "ScanFinding | None":
    """ADA/medium: page has no skip navigation link — WCAG 2.4.1 Bypass Blocks (v39).

    Keyboard-only users (motor disabilities, power users, screen reader users) must
    press Tab dozens of times to bypass repeated navigation menus and reach the main
    content on every page load. A skip navigation link — typically the very first
    focusable element — lets keyboard users jump directly to the primary content with
    a single keystroke. WCAG 2.4.1 (Level A) requires a mechanism to bypass blocks of
    content that are repeated across multiple pages. This is one of the most commonly
    cited ADA / Section 508 lawsuit triggers for SMB websites.
    """
    if SKIP_NAV_RE.search(pg_html):
        return None
    # Only flag when the page has a nav element (repeated nav makes this meaningful)
    if not NAV_ELEMENT_RE.search(pg_html):
        return None
    return ScanFinding(
        category="ada",
        severity="medium",
        title="Skip navigation link absent — keyboard trap risk",
        description=(
            "This page has navigation elements but no skip navigation link at the top of the page. "
            "Keyboard-only users and screen reader users must press Tab through every navigation "
            "item before reaching the main content area on every page load. WCAG 2.4.1 (Level A — "
            "the minimum required standard) mandates a bypass mechanism for repeated navigation "
            "blocks. The absence of a skip nav link is one of the top cited issues in ADA website "
            "accessibility lawsuits and DOJ enforcement letters."
        ),
        remediation=(
            "Add a visually hidden skip link as the very first element inside <body>:\n"
            '<a href="#main-content" class="skip-link">Skip to main content</a>\n'
            "Then add id=\"main-content\" to your <main> element. Style the link to appear on "
            "keyboard focus using CSS: .skip-link { position: absolute; left: -9999px; } "
            ".skip-link:focus { left: 0; }. In WordPress, add this to your theme's header.php "
            "before the nav markup. This is a 10-minute developer fix with no visual impact "
            "for mouse users."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet="<nav> present but no skip navigation link found",
            metadata={"has_nav": True, "skip_nav_found": False},
        ),
        confidence=0.78,
    )


def _check_structured_data_coverage(pages: "dict[str, str]", root_url: str) -> "ScanFinding | None":
    """SEO/low: <30% of crawled pages have any JSON-LD structured data (v39).

    Search engines use JSON-LD structured data to understand page content and generate
    rich results (star ratings, FAQs, breadcrumbs, sitelinks, etc.). Sites where only
    the homepage has structured data miss significant rich result opportunities on service,
    blog, product, and location pages. Low site-wide structured data coverage is a
    systematic SEO gap that affects every page in search results.
    """
    inner_pages = [u for u in pages if u != root_url]
    if len(inner_pages) < 2:
        return None  # Not enough pages crawled to make a meaningful assessment
    pages_with_ld = sum(1 for html in pages.values() if LD_JSON_BLOCK_RE.search(html))
    total_pages = len(pages)
    coverage_pct = pages_with_ld / total_pages if total_pages else 1.0
    if coverage_pct >= 0.30:
        return None
    return ScanFinding(
        category="seo",
        severity="low",
        title=f"Low structured data coverage across site ({pages_with_ld}/{total_pages} pages)",
        description=(
            f"Only {pages_with_ld} of {total_pages} crawled pages ({int(coverage_pct * 100)}%) "
            "contain JSON-LD structured data. Pages without schema markup miss opportunities for "
            "rich results (star ratings, FAQs, breadcrumbs, event dates) in Google Search. "
            "Competitors with comprehensive schema coverage gain enhanced SERP features that "
            "increase click-through rates by 15–30%. Service pages, blog posts, and location "
            "pages should each carry appropriate schema types (Service, Article, LocalBusiness)."
        ),
        remediation=(
            "Audit each page type and add appropriate JSON-LD schema blocks:\n"
            "• Service pages → Service or LocalBusiness schema\n"
            "• Blog posts → BlogPosting or Article schema\n"
            "• Location pages → LocalBusiness with address/hours\n"
            "• FAQ sections → FAQPage schema\n"
            "Use Google's Rich Results Test (https://search.google.com/test/rich-results) to "
            "validate each schema block after adding. In WordPress, the Yoast SEO or Rank Math "
            "plugins can automate schema generation for most page types without developer help."
        ),
        evidence=WebsiteEvidence(
            page_url=root_url,
            snippet=f"{pages_with_ld} of {total_pages} pages have JSON-LD",
            metadata={"pages_with_schema": pages_with_ld, "total_pages": total_pages,
                      "coverage_pct": round(coverage_pct, 2)},
        ),
        confidence=0.71,
    )


def _check_external_css_sri(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Security/low: ≥3 external CSS stylesheets loaded without SRI integrity= attribute (v39).

    Subresource Integrity (SRI) prevents supply chain attacks where a third-party CDN
    serving your CSS is compromised and begins injecting malicious styles (overlays,
    phishing forms, data-exfiltration payloads). Without integrity= attributes, any
    modification to the externally hosted stylesheet silently affects all visitors.
    OWASP A08:2021 (Software and Data Integrity Failures) covers this risk. While less
    exploited than script SRI, CSS supply chain attacks have occurred on production sites
    (e.g., Magecart-style skimmers via injected CSS).
    """
    ext_css_tags = EXTERNAL_CSS_LINK_RE.findall(pg_html)
    if len(ext_css_tags) < 3:
        return None
    missing_sri = [tag for tag in ext_css_tags if not CSS_INTEGRITY_ATTR_RE.search(tag)]
    if len(missing_sri) < 3:
        return None
    return ScanFinding(
        category="security",
        severity="low",
        title=f"External CSS stylesheets loaded without SRI integrity checks ({len(missing_sri)} found)",
        description=(
            f"This page loads {len(missing_sri)} external CSS stylesheets from third-party CDNs "
            "without Subresource Integrity (integrity=) attributes. If any of these CDN providers "
            "is compromised, attackers can inject malicious CSS — including overlay phishing forms, "
            "click-hijacking layers, or data-exfiltration styles — affecting every visitor. "
            "OWASP A08:2021 (Software and Data Integrity Failures) identifies missing SRI as a "
            "supply chain integrity risk. This is especially relevant for sites using Bootstrap "
            "CDN, Google Fonts, or Font Awesome via third-party CDN links."
        ),
        remediation=(
            "Generate SRI hashes for each external stylesheet and add integrity= and crossorigin= "
            "attributes:\n"
            '<link rel="stylesheet" href="https://cdn.example.com/style.css"\n'
            '      integrity="sha384-HASH_HERE" crossorigin="anonymous">\n'
            "Use the SRI Hash Generator at https://www.srihash.org/ to generate the correct "
            "integrity value for each stylesheet. Alternatively, self-host critical CSS to "
            "eliminate the dependency on external CDNs entirely — this also improves page load "
            "performance by removing third-party DNS lookups."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=f"{len(missing_sri)} external CSS links without integrity=",
            metadata={"external_css_count": len(ext_css_tags), "missing_sri_count": len(missing_sri)},
        ),
        confidence=0.67,
    )


def _check_html_lang_attribute_missing(pg_html: str, page_url: str, root_url: str) -> "ScanFinding | None":
    """ADA/high: HTML element has no lang attribute at all — WCAG 3.1.1 Language of Page (v39).

    Screen readers (JAWS, NVDA, VoiceOver) use the HTML lang attribute to select the
    correct pronunciation engine, reading speed, and accent for synthesized speech. Without
    a lang attribute, screen readers default to the OS/browser system language, which may
    be incorrect for the page content — causing the synthesizer to mispronounce every word
    in a foreign accent or use incorrect diphthongs. WCAG 3.1.1 (Level A) requires the
    human language of each page to be programmatically determinable. This is one of only
    three Level A failures that federal contractors and ADA-conscious SMBs are routinely
    cited for in accessibility audits. Complementary to _check_html_lang_region (which
    fires when lang IS present but lacks a region subtag like en-US).
    """
    if page_url != root_url:
        return None  # Only check homepage to avoid duplicate findings across pages
    if LANG_ATTR_PRESENT_RE.search(pg_html):
        return None
    # Confirm there is an html element at all (not just a fragment)
    if not re.search(r'<html\b', pg_html, re.IGNORECASE):
        return None
    return ScanFinding(
        category="ada",
        severity="high",
        title="HTML lang attribute completely absent — screen reader language failure",
        description=(
            "The <html> element on this page has no lang attribute at all. Screen readers "
            "(JAWS, NVDA, VoiceOver) rely on the HTML lang attribute to select the correct "
            "speech synthesis engine and pronunciation model. Without it, assistive technologies "
            "default to the operating system language setting, potentially mispronouncing all "
            "content in the wrong language or accent. WCAG 3.1.1 (Level A) — the minimum "
            "required accessibility standard — mandates that the language of each page be "
            "programmatically determinable. This failure is commonly cited in ADA demand letters "
            "and DOJ website accessibility enforcement actions."
        ),
        remediation=(
            'Add a lang attribute to your <html> opening tag:\n'
            '<html lang="en">\n'
            "For US English content, use lang=\"en\" or the more specific lang=\"en-US\". "
            "For bilingual sites, use lang= on the <html> element for the primary language "
            "and lang= on individual sections for secondary language content. "
            "In WordPress, the wp_language_attributes() function in theme header.php "
            "automatically outputs the correct lang attribute — verify it is present. "
            "This is a one-line change that takes under 5 minutes."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet='<html> — lang attribute not found',
            metadata={"lang_attr_present": False},
        ),
        confidence=0.95,
    )


def _check_form_fieldset_grouping(pg_html: str, page_url: str) -> "ScanFinding | None":
    """ADA/low: form with ≥6 inputs without <fieldset>/<legend> grouping — WCAG 1.3.1 (v39).

    Long forms with multiple input groups (personal info, shipping address, payment
    details, preferences) require semantic grouping via <fieldset> and <legend> so that
    screen readers can announce the group name when focus enters each section. Without
    fieldset grouping, screen readers read each label in isolation with no contextual
    framing — users filling out a 10+ field form cannot tell which section they are in.
    WCAG 1.3.1 (Info and Relationships, Level A) requires that structural relationships
    conveyed visually be programmatically determinable. This particularly affects radio
    button groups and multi-step contact/checkout forms.
    """
    # Only check pages with forms
    if not FORM_RE.search(pg_html):
        return None
    # Count all input elements (text, email, tel, etc. — not hidden/submit)
    all_inputs = re.findall(
        r'<(?:input|textarea|select)\b[^>]*>',
        pg_html,
        re.IGNORECASE,
    )
    non_hidden = [i for i in all_inputs if not re.search(r'\btype=["\']hidden["\']', i, re.IGNORECASE)]
    if len(non_hidden) < 6:
        return None
    if FIELDSET_LEGEND_RE.search(pg_html):
        return None
    return ScanFinding(
        category="ada",
        severity="low",
        title=f"Long form ({len(non_hidden)} fields) missing fieldset grouping",
        description=(
            f"This page contains a form with {len(non_hidden)} input fields but no <fieldset> "
            "grouping elements. Screen reader users navigating multi-section forms need "
            "<fieldset>/<legend> markup to understand which group of fields they are currently "
            "filling out (e.g. 'Contact Information', 'Shipping Address', 'Billing Details'). "
            "Without this grouping, each field is read in isolation with no structural context. "
            "WCAG 1.3.1 (Level A) requires that information and relationships conveyed visually "
            "through layout also be programmatically determinable. This also improves form "
            "completion rates for sighted users by providing visual section boundaries."
        ),
        remediation=(
            "Wrap logically related input groups in <fieldset> with a <legend> label:\n"
            "<fieldset>\n"
            "  <legend>Contact Information</legend>\n"
            "  <!-- name, email, phone fields -->\n"
            "</fieldset>\n"
            "Use one fieldset per logical group (personal info, address, preferences). "
            "For radio button or checkbox groups, fieldset/legend is always required by WCAG. "
            "In WordPress form plugins (Gravity Forms, WPForms), fieldset grouping can be "
            "configured in the field group settings without custom code."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=f"{len(non_hidden)} form inputs, no <fieldset> found",
            metadata={"input_count": len(non_hidden), "fieldset_present": False},
        ),
        confidence=0.71,
    )


def _check_manifest_json_missing(pg_html: str, page_url: str, root_url: str) -> "ScanFinding | None":
    """Performance/low: homepage missing web app manifest link — lost PWA/homescreen capability (v40).

    A web app manifest (<link rel="manifest" href="/manifest.json">) enables browsers to
    offer an 'Add to Home Screen' prompt on Android/Chrome, giving the business a free
    app-like icon on a customer's phone. For service businesses (salons, restaurants,
    home services) whose customers research and re-visit from mobile, this is a missed
    engagement touchpoint. It also signals to Google that the site meets progressive web
    app quality criteria, which can influence Core Web Vitals thresholds.
    Fires on root URL only — a manifest is a site-wide asset declared on the homepage.
    """
    if page_url != root_url:
        return None
    if MANIFEST_LINK_RE.search(pg_html):
        return None
    return ScanFinding(
        category="performance",
        severity="low",
        title="Missing web app manifest — no 'Add to Home Screen' capability",
        description=(
            "The homepage has no <link rel=\"manifest\"> pointing to a web app manifest file. "
            "Without a manifest, mobile browsers cannot offer the 'Add to Home Screen' install "
            "prompt that places your business icon directly on a customer's phone. For service "
            "businesses (restaurants, salons, home services), this is a free retention touchpoint "
            "that drives repeat visits without paid advertising. A manifest also enables browser-"
            "level pre-caching that can improve load speed for returning visitors on mobile networks. "
            "Progressive Web App (PWA) support is a signal Google uses when evaluating site quality."
        ),
        remediation=(
            "Create a manifest.json file at your site root with: name, short_name, start_url, "
            "display (set to 'standalone'), background_color, theme_color, and icons (at least "
            "192×192 and 512×512 PNG). Then add <link rel=\"manifest\" href=\"/manifest.json\"> "
            "inside the <head> of every page. In WordPress, the WP PWA or Super Progressive Web "
            "Apps plugin generates this automatically. In Squarespace or Wix, use a code injection "
            "block in site settings to add the link tag. Validate with Chrome DevTools > Application "
            "> Manifest to confirm the file is detected."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet="No <link rel=\"manifest\"> found in page <head>",
            metadata={"manifest_detected": False},
        ),
        confidence=0.65,
    )


def _check_hreflang_inconsistency(pages: dict[str, str]) -> "ScanFinding | None":
    """SEO/low: cross-page hreflang inconsistency — partial international SEO implementation (v40).

    hreflang annotations signal to Google which language/region each page targets and
    help prevent duplicate-content penalties for sites that serve multiple languages or
    regional variants. However, hreflang must be declared consistently across all pages
    in the alternate set — if only some pages declare hreflang while others omit it,
    Google may ignore the annotations entirely, leaving international visitors on the
    wrong language variant and leaking cross-regional ranking signals.
    """
    if len(pages) < 2:
        return None
    pages_with_hreflang = [url for url, html in pages.items() if HREFLANG_RE.search(html)]
    if not pages_with_hreflang:
        return None
    ratio = len(pages_with_hreflang) / len(pages)
    if ratio >= 0.80:
        return None  # consistent enough — at least 80% of pages declare hreflang
    return ScanFinding(
        category="seo",
        severity="low",
        title="Inconsistent hreflang annotations across crawled pages",
        description=(
            f"hreflang link tags were found on {len(pages_with_hreflang)} of {len(pages)} crawled pages "
            f"({int(ratio * 100)}% coverage). For hreflang to work correctly, every page in an "
            "alternate set must declare hreflang annotations for all language/region variants — "
            "not just a subset. Partial implementation causes Google to ignore the annotations "
            "entirely, which means international visitors may land on the wrong language version "
            "and ranking signals are not properly consolidated across regional variants. "
            "This can silently harm international organic traffic even when individual pages "
            "look correct in isolation."
        ),
        remediation=(
            "Audit all pages in your alternate set and add consistent hreflang <link> tags to "
            "every page's <head>: <link rel=\"alternate\" hreflang=\"en-US\" href=\"https://example.com/\"> "
            "and <link rel=\"alternate\" hreflang=\"x-default\" href=\"https://example.com/\">. "
            "Use Google Search Console > International Targeting > Language to verify hreflang "
            "is detected correctly. Plugins like WPML or Polylang manage hreflang automatically "
            "for WordPress. Validate with the hreflang Tag Testing Tool at aleydasolis.com/tools."
        ),
        evidence=WebsiteEvidence(
            page_url=pages_with_hreflang[0],
            snippet=f"hreflang found on {len(pages_with_hreflang)}/{len(pages)} pages",
            metadata={"pages_with_hreflang": len(pages_with_hreflang), "total_pages": len(pages)},
        ),
        confidence=0.74,
    )


def _check_self_referential_canonical_missing(pg_html: str, page_url: str, root_url: str) -> "ScanFinding | None":
    """SEO/low: inner page with content has no canonical tag — risks search engine URL variant confusion (v40).

    This is distinct from _check_canonical_mismatch (which fires when a canonical points to
    the WRONG URL). This check fires when an inner page has no canonical tag at all.
    Without any canonical, search engines may index multiple URL variants of the same page
    (e.g. with/without trailing slash, with query strings, via HTTP redirect chains) as
    separate documents, diluting ranking signals and potentially causing duplicate content
    issues that suppress the intended URL in search results.
    """
    if page_url == root_url:
        return None  # Homepage canonical is checked separately
    stripped_words = WORD_CONTENT_RE.findall(re.sub(r"<[^>]+>", " ", pg_html))
    if len(stripped_words) < 100:
        return None  # Skip near-empty pages
    if CANONICAL_RE.search(pg_html[:5000]):
        return None
    return ScanFinding(
        category="seo",
        severity="low",
        title="Inner page missing self-referential canonical tag",
        description=(
            "This inner page contains meaningful content but has no <link rel=\"canonical\"> tag. "
            "Without a canonical, search engines may index multiple URL variants of this page "
            "as separate documents — e.g. with and without trailing slash, with session ID "
            "query parameters, or from HTTP redirect chains. Each variant competes with the "
            "others for ranking, diluting PageRank and potentially surfacing the 'wrong' URL in "
            "search results. Self-referential canonicals (a page pointing canonical to itself) "
            "are a simple defensive SEO measure that confirms the intended indexable URL version "
            "and is recommended by Google for all indexable pages."
        ),
        remediation=(
            "Add a self-referential canonical to this page's <head>: "
            "<link rel=\"canonical\" href=\"https://yourdomain.com/this-page-url/\">. "
            "Use the exact preferred URL format you want indexed (https, with or without trailing "
            "slash, without query parameters). In WordPress with Yoast SEO or Rank Math, canonical "
            "tags are added automatically — verify the plugin is active and not suppressed. "
            "In Squarespace and Wix, canonical tags are managed automatically; ensure you haven't "
            "overridden the default behavior in custom code injections."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet="No canonical link tag found on inner content page",
            metadata={"has_canonical": False, "word_count": len(stripped_words)},
        ),
        confidence=0.80,
    )


def _check_excessive_dom_size(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Performance/low-medium: estimated DOM node count ≥800 — matches Google Lighthouse threshold (v40).

    Google Lighthouse flags DOM trees with more than 800 nodes as 'Avoid an excessive DOM size',
    and critical at >1,500 nodes. Large DOMs slow layout, style recalculation, and reflows
    triggered by JavaScript interactions. This is a common performance bottleneck on template-
    heavy WordPress sites with dense shortcode output, page builders (Divi, Elementor) that
    generate nested wrapper divs, and WooCommerce product pages with complex attribute grids.
    We use a regex-based element count as a heuristic — every HTML opening tag counts as
    approximately one DOM node, giving a conservative estimate.
    """
    element_count = len(HTML_ELEMENT_RE.findall(pg_html))
    if element_count < 800:
        return None
    severity = "medium" if element_count >= 1500 else "low"
    return ScanFinding(
        category="performance",
        severity=severity,
        title=f"Excessive DOM size detected (~{element_count} elements)",
        description=(
            f"This page contains approximately {element_count} HTML elements — "
            f"{'well above' if element_count >= 1500 else 'above'} Google Lighthouse's "
            "critical threshold of 1,500 nodes (warning at 800 nodes). Large DOM trees slow "
            "layout recalculation, style matching, and JavaScript-triggered reflows. This is "
            "especially harmful on mobile devices where CPU is limited. Common causes include "
            "page builders (Elementor, Divi, WPBakery) generating deeply nested wrapper divs, "
            "complex navigation menus repeated in markup, and large product grids rendered "
            "without pagination or virtual scrolling. Google's Core Web Vitals (INP, LCP) "
            "are directly affected by DOM complexity during user interactions."
        ),
        remediation=(
            "Step 1: Run Chrome DevTools > Performance > Record to identify which JavaScript "
            "operations trigger the most layout recalculations. "
            "Step 2: Audit your page builder markup — Elementor and Divi sites often have "
            "5–7 wrapper divs per content block that can be reduced with custom CSS. "
            "Step 3: Implement infinite scroll or 'Load More' pagination on product/blog grids "
            "to avoid rendering all items on initial page load. "
            "Step 4: Remove hidden markup (e.g. off-canvas menus, collapsed sections) that "
            "stays in the DOM even when not visible — use CSS display:none sparingly. "
            "Target: under 800 DOM nodes on all key landing pages per Google's recommendation."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=f"~{element_count} HTML elements estimated via element count heuristic",
            metadata={"estimated_dom_nodes": element_count, "lighthouse_threshold": 800},
        ),
        confidence=0.68,
    )


def _check_input_pattern_missing(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Conversion/low: phone/zip inputs missing semantic type or pattern attr — missed mobile UX (v40).

    Inputs for phone numbers and postal codes named with obvious identifiers
    (name="phone", placeholder="ZIP code", etc.) but using type="text" without a
    pattern attribute miss two key mobile UX improvements: (1) the correct soft
    keyboard (numeric/phone keypad vs. full QWERTY keyboard), and (2) native browser
    validation before form submission. Both reduce friction and form abandonment — on
    mobile where the keyboard covers 40–60% of screen space, the wrong keyboard type
    is a tangible conversion barrier. HTML5 type=tel and type=number are broadly
    supported since 2013 and require no JavaScript.
    """
    if not FORM_RE.search(pg_html):
        return None
    phone_zip_inputs = PHONE_ZIP_INPUT_RE.findall(pg_html)
    if not phone_zip_inputs:
        return None
    # Check if the same section of HTML already has semantic type or pattern attributes
    if SEMANTIC_INPUT_TYPE_RE.search(pg_html):
        return None
    count = len(phone_zip_inputs)
    return ScanFinding(
        category="conversion",
        severity="low",
        title=f"Phone/zip input fields missing type=tel or pattern validation ({count} found)",
        description=(
            f"Found {count} input field(s) for phone numbers or postal codes using type='text' "
            "without a pattern attribute. On mobile devices, this displays a full QWERTY keyboard "
            "instead of the optimized numeric/phone keypad — a friction point that increases "
            "keystrokes and form abandonment for mobile users who represent the majority of "
            "service business web traffic. Without a pattern attribute, browsers also cannot "
            "provide native pre-submission validation, meaning users only discover errors after "
            "submitting (or not at all if the form accepts any text format). Both issues are "
            "fixable with a single HTML attribute change per field."
        ),
        remediation=(
            "For phone number inputs, change type='text' to type='tel': "
            "<input type=\"tel\" name=\"phone\" placeholder=\"(555) 555-5555\" "
            "pattern=\"[0-9\\s\\(\\)\\-\\+]{7,15}\">. "
            "For postal code inputs, use type='text' with a pattern: "
            "<input type=\"text\" name=\"zip\" pattern=\"[0-9]{5}(-[0-9]{4})?\" "
            "inputmode=\"numeric\" maxlength=\"10\">. "
            "The inputmode=\"numeric\" attribute shows a numeric keypad on mobile while "
            "keeping the text field for formatted ZIP+4 codes. "
            "In most form plugins (Gravity Forms, WPForms, Formidable), change the field type "
            "in the form editor — no custom code required."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=f"{count} phone/zip text inputs without semantic type or pattern",
            metadata={"phone_zip_inputs_found": count, "semantic_type_present": False},
        ),
        confidence=0.71,
    )


def _check_meta_viewport_missing(pg_html: str, page_url: str, root_url: str) -> "ScanFinding | None":
    """ADA/high: homepage missing <meta name='viewport'> — mobile rendering broken (v41).

    Only fires on the root URL. Complementary to _check_viewport_user_scalable (v27)
    which detects user-scalable=no; this check fires when the viewport meta tag is
    entirely absent (more severe — mobile layout completely broken without it).
    """
    if page_url.rstrip("/") != root_url.rstrip("/"):
        return None
    if VIEWPORT_RE.search(pg_html):
        return None
    head_match = HEAD_SECTION_RE.search(pg_html)
    snippet = ""
    if head_match:
        head_html = head_match.group(1)[:800]
        snippet = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", head_html)).strip()[:180]
    return ScanFinding(
        category="ada",
        severity="high",
        title="No Viewport Meta Tag — Mobile Rendering Broken",
        description=(
            "The homepage is missing a <meta name='viewport'> tag. Without this tag, "
            "mobile browsers render the page at desktop width and shrink it to fit the screen, "
            "making text tiny and tap targets impossibly small. This directly violates WCAG 2.1 SC "
            "1.4.4 Resize Text and is a disqualifying mobile usability issue in Google's Core Web "
            "Vitals mobile-friendliness evaluation. Mobile visitors — typically 60–70% of SMB "
            "website traffic — experience a frustrating pinch-to-zoom interface that dramatically "
            "reduces contact form completions and phone call conversions from mobile searchers."
        ),
        remediation=(
            "Add <meta name='viewport' content='width=device-width, initial-scale=1'> "
            "inside your <head> section, immediately after the <meta charset> declaration. "
            "In WordPress: this is typically output by your active theme in header.php — "
            "if it's missing, your theme may be broken, outdated, or you're using a legacy "
            "custom template. Switching to a modern responsive theme (Astra, GeneratePress, "
            "Kadence) will add this automatically. In Squarespace and Wix, the platform "
            "adds the viewport tag automatically — its absence suggests a custom code injection "
            "or template override is overwriting the <head>."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=snippet or "No <meta name='viewport'> found in page <head>",
            metadata={"viewport_present": False},
        ),
        confidence=0.93,
    )


def _check_svg_icon_aria_missing(pg_html: str, page_url: str) -> "ScanFinding | None":
    """ADA/low-medium: SVG elements without proper accessibility attributes (v41).

    SVG elements that are neither marked as decorative (aria-hidden='true') nor
    meaningful images (role='img' + aria-label) cause screen reader confusion.
    Screen readers may read SVG path code verbatim or skip graphics entirely,
    violating WCAG 1.1.1 Non-text Content.
    """
    total_svgs = len(SVG_OPEN_RE.findall(pg_html))
    if total_svgs == 0:
        return None
    hidden_svgs = len(SVG_ARIA_HIDDEN_RE.findall(pg_html))
    img_svgs = len(SVG_ROLE_IMG_RE.findall(pg_html))
    unprotected = total_svgs - hidden_svgs - img_svgs
    if unprotected < 2:
        return None
    severity = "medium" if unprotected >= 4 else "low"
    return ScanFinding(
        category="ada",
        severity=severity,
        title="SVG Icons Missing Accessibility Attributes",
        description=(
            f"Found {total_svgs} SVG element(s) on this page, of which {unprotected} lack both "
            "aria-hidden='true' (for decorative icons) and role='img' with aria-label (for "
            "meaningful graphics). Screen readers may read raw SVG path data verbosely or skip "
            "the graphic entirely, violating WCAG 1.1.1 Non-text Content. Icon-heavy navigation "
            "menus and social media icon bars are common sources of screen reader confusion when "
            "SVGs are not explicitly marked as decorative or given accessible labels."
        ),
        remediation=(
            "For decorative SVG icons (purely visual, adjacent text already explains the action): "
            "add aria-hidden='true' and focusable='false' to the <svg> tag. "
            "For meaningful SVG illustrations or charts that convey information without surrounding text: "
            "add role='img' and aria-label='Description of what this image shows' to the <svg> tag. "
            "In WordPress with Font Awesome or Dashicons: Font Awesome 6+ includes aria-hidden by "
            "default — ensure you're using the latest version. For theme-generated SVG icons, "
            "edit the icon output function in functions.php to add aria-hidden='true'. "
            "This improves WCAG 1.1.1 compliance and prevents screen readers from announcing "
            "meaningless SVG path coordinates to visually impaired users."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=f"{unprotected} of {total_svgs} SVG elements lack aria-hidden or role=img labeling",
            metadata={
                "total_svgs": total_svgs,
                "aria_hidden_svgs": hidden_svgs,
                "role_img_svgs": img_svgs,
                "unprotected_svgs": unprotected,
            },
        ),
        confidence=0.72,
    )


def _check_long_content_no_back_to_top(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Conversion/low: long-form page without back-to-top navigation — UX friction (v41).

    Pages with ≥1500 estimated words but no back-to-top anchor or button increase
    exit rates for mobile visitors who lose their place scrolling through long content.
    """
    word_count = len(WORD_CONTENT_RE.findall(pg_html))
    if word_count < 1500:
        return None
    if BACK_TO_TOP_RE.search(pg_html):
        return None
    return ScanFinding(
        category="conversion",
        severity="low",
        title="No Back-to-Top Navigation on Long-Form Page",
        description=(
            f"This page contains approximately {word_count} words but lacks a 'Back to Top' "
            "button or scroll anchor. Long-form pages (services, about us, FAQ, portfolio) "
            "without navigation aids increase exit rates as mobile visitors lose their place "
            "while scrolling and cannot quickly return to the top navigation or primary CTA. "
            "Mobile users in particular benefit from back-to-top navigation on content-heavy "
            "pages, reducing friction when they finish reading and want to take action."
        ),
        remediation=(
            "Add a sticky 'Back to Top' element: include id='top' on your <body> or <header>, "
            "then add <a href='#top' class='back-to-top' aria-label='Back to top'>↑</a> "
            "with CSS to position it fixed at the bottom-right of the viewport. "
            "In WordPress: plugins like 'WPFront Scroll Top' or 'Back To Top Button' add "
            "this in under 2 minutes with no code required. "
            "In Wix: add a 'Back to Top' element from the Add panel under Interactive Elements. "
            "In Squarespace: enable the 'Back to Top Button' option in Style Editor settings. "
            "This reduces scroll fatigue and improves mobile conversion on service and FAQ pages."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=f"~{word_count} estimated words, no back-to-top anchor detected",
            metadata={"estimated_word_count": word_count},
        ),
        confidence=0.65,
    )


def _check_multiple_canonical_tags(pg_html: str, page_url: str) -> "ScanFinding | None":
    """SEO/medium: multiple competing canonical tags on a page — Google ignores both (v41).

    When two or more <link rel='canonical'> tags appear on the same page (commonly
    from plugin conflicts), Google may disregard all of them and select a canonical
    arbitrarily, silently undermining link equity consolidation.
    """
    canonical_tags = CANONICAL_RE.findall(pg_html)
    count = len(canonical_tags)
    if count < 2:
        return None
    unique_hrefs = list(dict.fromkeys(canonical_tags))
    snippet = f"Canonical tag 1: {unique_hrefs[0][:100]}"
    if len(unique_hrefs) > 1:
        snippet += f" | Canonical tag 2: {unique_hrefs[1][:100]}"
    return ScanFinding(
        category="seo",
        severity="medium",
        title="Multiple Competing Canonical Tags Detected",
        description=(
            f"This page contains {count} competing <link rel='canonical'> tags. "
            "When multiple canonical tags are present, Google may ignore all of them "
            "and select a preferred URL based on internal link equity signals — "
            "overriding your intended SEO consolidation strategy. Multiple canonicals "
            "typically occur when two plugins (e.g. Yoast SEO + Rank Math, or a theme "
            "plus an SEO plugin) each inject their own canonical tag, or when a CDN or "
            "caching layer adds a second canonical on top of an existing one."
        ),
        remediation=(
            "Audit your SEO plugin settings and theme header output to identify which source "
            "is adding each canonical tag. Disable canonical output from all but one source. "
            "In WordPress: check your active SEO plugin (Yoast, Rank Math, All-in-One SEO) "
            "and your theme's head settings — only one should output canonicals. "
            "Use View Page Source in Chrome to count all canonical tags on the live rendered page. "
            "After resolving the conflict, validate with Google's Rich Results Test or "
            "Google Search Console URL Inspection tool to confirm a single canonical is reported."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=snippet,
            metadata={"canonical_count": count, "canonical_hrefs": unique_hrefs[:3]},
        ),
        confidence=0.88,
    )


def _check_iframe_sandbox_missing(pg_html: str, page_url: str) -> "ScanFinding | None":
    """Security/low: external iframes without sandbox attribute — OWASP A05:2021 (v41).

    Third-party iframes (maps, videos, forms, widgets) without a 'sandbox' attribute
    can access parent document cookies, navigate the top-level window, and run
    scripts with the parent page's trust context — OWASP A05:2021 Security Misconfiguration.
    """
    total_external = len(IFRAME_EXTERNAL_SRC_RE.findall(pg_html))
    if total_external < 2:
        return None
    sandboxed = len(IFRAME_SANDBOX_RE.findall(pg_html))
    unsandboxed = total_external - sandboxed
    if unsandboxed < 2:
        return None
    return ScanFinding(
        category="security",
        severity="low",
        title="Embedded Third-Party Iframes Missing Sandbox Attribute",
        description=(
            f"Found {total_external} external-origin iframe(s) on this page, of which "
            f"{unsandboxed} lack the 'sandbox' attribute. Unsandboxed third-party iframes "
            "can access parent document cookies, navigate the top-level window, submit forms, "
            "and execute scripts with the embedding page's trust context — a risk vector "
            "identified in OWASP A05:2021 (Security Misconfiguration). Common embedding "
            "use cases (Google Maps, YouTube videos, Typeform, payment widgets) all function "
            "correctly with appropriate sandbox permissions applied."
        ),
        remediation=(
            "Add the sandbox attribute to third-party iframes and grant only the necessary permissions. "
            "For Google Maps embeds: <iframe sandbox='allow-scripts allow-same-origin' ...>. "
            "For YouTube embeds: <iframe sandbox='allow-scripts allow-same-origin allow-presentation' ...>. "
            "For contact or survey forms (Typeform, JotForm): <iframe sandbox='allow-scripts allow-forms "
            "allow-same-origin' ...>. "
            "Note: adding sandbox without allow-same-origin disables cookie access for the iframe "
            "content — test each embed after adding sandbox to confirm expected functionality. "
            "In WordPress page builders, use a custom HTML block to add the sandbox attribute "
            "if the embed shortcode does not support it natively."
        ),
        evidence=WebsiteEvidence(
            page_url=page_url,
            snippet=f"{unsandboxed} of {total_external} external iframes lack sandbox attribute",
            metadata={
                "total_external_iframes": total_external,
                "sandboxed_count": sandboxed,
                "unsandboxed_count": unsandboxed,
            },
        ),
        confidence=0.73,
    )


def _fallback_scan_result(
    *,
    base_url: str,
    out_dir: Path,
    reason: str,
    tls: dict[str, Any],
    dns_auth: dict[str, Any],
) -> dict[str, Any]:
    """Build a non-crashing scan result when the target site blocks or fails HTTP fetches."""
    shots_dir = out_dir / "screenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    shot_map: dict[str, str] = {}
    for i in range(1, 4):
        shot_map[f"{base_url}#fallback_{i}"] = _capture_placeholder_screenshot(
            shots_dir / f"fallback_{i}.png",
            "Website access blocked or unavailable",
            f"Deep page crawl could not complete: {reason[:220]}",
        )

    findings: list[ScanFinding] = [
        ScanFinding(
            category="security",
            severity="high",
            title="Website blocked automated assessment requests",
            description=(
                "The site did not return accessible HTML for the initial crawl request. "
                f"Observed error: {reason[:260]}. "
                "When automated checks are blocked, security posture cannot be fully verified and urgent issues may remain hidden."
            ),
            remediation=(
                "Allowlist the assessment user-agent/IP or provide a temporary public/staging URL. "
                "Re-run this report after access is granted to validate headers, forms, and page-level controls."
            ),
            evidence=WebsiteEvidence(page_url=base_url, screenshot_path=next(iter(shot_map.values()))),
            confidence=0.94,
        ),
        ScanFinding(
            category="seo",
            severity="medium",
            title="SEO crawl depth limited by access restrictions",
            description=(
                "The crawler could not access key pages, so indexability, metadata, heading structure, and internal-link coverage "
                "could not be fully measured."
            ),
            remediation=(
                "Temporarily allow crawl access for '/', '/about', '/services', and '/contact', then re-run the audit "
                "to capture actionable technical and on-page SEO findings."
            ),
            evidence=WebsiteEvidence(page_url=base_url, metadata={"scan_error": reason[:260]}),
            confidence=0.88,
        ),
        ScanFinding(
            category="ada",
            severity="medium",
            title="Accessibility automation could not execute on blocked pages",
            description=(
                "WCAG checks (alt text, label association, keyboard flow, and landmark structure) require page content. "
                "Because the site was inaccessible, accessibility risk remains unknown."
            ),
            remediation=(
                "Provide crawl access or a staging URL and run a full accessibility pass before publishing major content changes."
            ),
            evidence=WebsiteEvidence(page_url=base_url, metadata={"scan_error": reason[:260]}),
            confidence=0.84,
        ),
        ScanFinding(
            category="conversion",
            severity="medium",
            title="Lead-generation UX could not be validated due page access failures",
            description=(
                "CTA hierarchy, trust signals, and form friction analysis depends on accessible rendered pages. "
                "With limited access, conversion leakage may exist but cannot be quantified from this run."
            ),
            remediation=(
                "Grant temporary crawl access and include homepage + contact/service pages in the next run "
                "to get prioritized conversion fixes."
            ),
            evidence=WebsiteEvidence(page_url=base_url, metadata={"scan_error": reason[:260]}),
            confidence=0.86,
        ),
    ]

    if any((dns_auth.get(k) or "") == "missing" for k in ("spf", "dkim", "dmarc")):
        missing = [k.upper() for k in ("spf", "dkim", "dmarc") if (dns_auth.get(k) or "") == "missing"]
        findings.append(
            ScanFinding(
                category="email_auth",
                severity="high" if "DMARC" in missing else "medium",
                title=f"Email authentication gaps detected ({', '.join(missing)})",
                description=(
                    "DNS checks indicate missing email-auth records. Missing SPF/DKIM/DMARC increases spoofing risk and can reduce inbox placement."
                ),
                remediation=(
                    "Publish SPF, DKIM, and DMARC records, then validate alignment with a mailbox header test and DMARC aggregate reports."
                ),
                evidence=WebsiteEvidence(page_url=base_url, metadata=dns_auth),
                confidence=0.90,
            )
        )
    elif any((dns_auth.get(k) or "") == "unknown" for k in ("spf", "dkim", "dmarc")):
        unknown = [k.upper() for k in ("spf", "dkim", "dmarc") if (dns_auth.get(k) or "") == "unknown"]
        findings.append(
            ScanFinding(
                category="email_auth",
                severity="low",
                title=f"Email authentication could not be fully verified ({', '.join(unknown)})",
                description=(
                    "DNS checks were inconclusive for some email-auth records in this run. "
                    "This is an uncertainty signal, not proof that records are missing."
                ),
                remediation=(
                    "Re-run DNS checks from a stable resolver and validate email headers from a live outbound message."
                ),
                evidence=WebsiteEvidence(page_url=base_url, metadata=dns_auth),
                confidence=0.6,
            )
        )
    else:
        findings.append(
            ScanFinding(
                category="email_auth",
                severity="low",
                title="Email authentication appears present but alignment not verified",
                description=(
                    "SPF, DKIM, and DMARC records were detected at DNS level. Message-level alignment still needs header validation from live outbound mail."
                ),
                remediation=(
                    "Send a test email to Gmail/Outlook and verify SPF=pass, DKIM=pass, and DMARC=pass with aligned domains."
                ),
                evidence=WebsiteEvidence(page_url=base_url, metadata=dns_auth),
                confidence=0.74,
            )
        )

    if not tls.get("ok"):
        findings.append(
            ScanFinding(
                category="security",
                severity="high",
                title="TLS certificate or handshake issue",
                description=(
                    "The domain did not complete a trusted TLS handshake, which can trigger browser warnings and suppress conversions."
                ),
                remediation="Review certificate chain, expiry, hostname coverage, and TLS configuration.",
                evidence=WebsiteEvidence(page_url=base_url, metadata=tls),
                confidence=0.88,
            )
        )

    _sanitize_findings(findings)
    for finding in findings:
        validate_finding(finding)

    return {
        "base_url": base_url,
        "pages": [base_url],
        "screenshots": shot_map,
        "tls": tls,
        "dns_auth": dns_auth,
        "robots": {"found": False, "disallow_all": False, "has_sitemap": False, "raw": ""},
        "exposed_files": [],
        "load_times": {},
        "scan_error": reason,
        "findings": findings,
        "finding_dicts": [_finding_to_dict(f) for f in findings],
    }


def _run_site_audit_seo_external(*, base_url: str, out_dir: Path) -> list[ScanFinding]:
    """Run site-audit-seo (npm) and convert key crawler flags into findings."""
    audit_dir = out_dir / "site_audit_seo"
    audit_dir.mkdir(parents=True, exist_ok=True)
    report_json = audit_dir / "site_audit.json"
    npx_bin = shutil.which("npx")
    if not npx_bin:
        for candidate in (
            "/opt/homebrew/bin/npx",
            "/usr/local/bin/npx",
            str(Path.home() / ".nvm/versions/node/v21.1.0/bin/npx"),
        ):
            if Path(candidate).exists():
                npx_bin = candidate
                break
    if not npx_bin:
        return []

    env = dict(os.environ)
    env["PATH"] = ":".join(
        [
            str(Path(npx_bin).parent),
            env.get("PATH", ""),
        ]
    )

    cmd = [
        npx_bin,
        "--yes",
        "site-audit-seo",
        "-u",
        base_url,
        "-p",
        "seo-minimal",
        "-d",
        "2",
        "-c",
        "4",
        "-m",
        "20",
        "--json",
        "--no-remove-json",
        "--no-remove-csv",
        "--out-dir",
        str(audit_dir),
        "--out-name",
        "site_audit",
    ]
    try:
        # Tool serves a local report after saving JSON; timeout is expected and acceptable.
        subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=False, env=env)
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        return []

    if not report_json.exists():
        return []

    try:
        payload = json.loads(report_json.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return []

    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        return []

    def _urls_where(pred: Any) -> list[str]:
        urls: list[str] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            try:
                if pred(it):
                    u = str(it.get("url") or "").strip()
                    if u:
                        urls.append(u)
            except Exception:
                continue
        return urls

    missing_titles = _urls_where(lambda it: not str(it.get("title") or "").strip())
    missing_descriptions = _urls_where(lambda it: not str(it.get("description") or "").strip())
    missing_h1 = _urls_where(lambda it: not str(it.get("h1") or "").strip())
    broken_urls = _urls_where(lambda it: int(it.get("status") or 0) >= 400)
    non_canonical = _urls_where(lambda it: str(it.get("is_canonical") or "").lower() in {"false", "0"})

    findings: list[ScanFinding] = []
    if missing_titles:
        findings.append(
            ScanFinding(
                category="seo",
                severity="high" if len(missing_titles) >= 3 else "medium",
                title="site-audit-seo: missing title tags across crawled pages",
                description=(
                    f"External SEO crawl detected {len(missing_titles)} page(s) missing a title tag, "
                    "which weakens topical relevance and SERP click-through performance."
                ),
                remediation="Add one unique title tag per affected page with clear keyword intent and brand.",
                evidence=WebsiteEvidence(page_url=missing_titles[0], metadata={"tool": "site-audit-seo", "affected_pages": len(missing_titles)}),
                confidence=0.88,
            )
        )
    if missing_descriptions:
        findings.append(
            ScanFinding(
                category="seo",
                severity="medium",
                title="site-audit-seo: missing meta descriptions across crawled pages",
                description=(
                    f"External SEO crawl detected {len(missing_descriptions)} page(s) without meta descriptions, "
                    "which reduces snippet quality and organic click-through potential."
                ),
                remediation="Write a distinct 120–160 character meta description for each affected page.",
                evidence=WebsiteEvidence(page_url=missing_descriptions[0], metadata={"tool": "site-audit-seo", "affected_pages": len(missing_descriptions)}),
                confidence=0.86,
            )
        )
    if missing_h1:
        findings.append(
            ScanFinding(
                category="seo",
                severity="medium",
                title="site-audit-seo: missing H1 headings on crawled pages",
                description=(
                    f"External SEO crawl detected {len(missing_h1)} page(s) without an H1 heading, "
                    "which weakens the primary topical signal per page."
                ),
                remediation="Add exactly one descriptive H1 heading per affected URL.",
                evidence=WebsiteEvidence(page_url=missing_h1[0], metadata={"tool": "site-audit-seo", "affected_pages": len(missing_h1)}),
                confidence=0.85,
            )
        )
    if non_canonical:
        findings.append(
            ScanFinding(
                category="seo",
                severity="medium",
                title="site-audit-seo: canonical inconsistencies detected",
                description=(
                    f"External SEO crawl detected {len(non_canonical)} page(s) with canonical URL inconsistencies, "
                    "which can split ranking signals across duplicates."
                ),
                remediation="Set clean self-referencing canonical URLs on all indexable pages.",
                evidence=WebsiteEvidence(page_url=non_canonical[0], metadata={"tool": "site-audit-seo", "affected_pages": len(non_canonical)}),
                confidence=0.82,
            )
        )
    if broken_urls:
        findings.append(
            ScanFinding(
                category="seo",
                severity="high" if len(broken_urls) >= 2 else "medium",
                title="site-audit-seo: broken pages or links detected",
                description=(
                    f"External SEO crawl found {len(broken_urls)} URL(s) returning 4xx/5xx responses, "
                    "which harms crawlability and user trust."
                ),
                remediation="Fix or redirect broken URLs and re-run crawl to confirm healthy status codes.",
                evidence=WebsiteEvidence(page_url=broken_urls[0], metadata={"tool": "site-audit-seo", "affected_pages": len(broken_urls)}),
                confidence=0.90,
            )
        )
    return findings


def _run_light_scan_pipeline(
    *,
    base_url: str,
    root_url: str,
    pages: dict[str, str],
    load_times: dict[str, float],
    tls: dict[str, Any],
    dns_auth: dict[str, Any],
    out_dir: Path,
) -> dict[str, Any]:
    """Fast pre-offer scan for pain-point discovery before full report."""
    root_html = pages.get(root_url, "")
    findings: list[ScanFinding] = []

    # Security headers
    sec_headers = {
        "strict-transport-security": "missing",
        "content-security-policy": "missing",
        "x-frame-options": "missing",
        "x-content-type-options": "missing",
    }
    try:
        with httpx.Client(timeout=8.0, follow_redirects=True) as client:
            r = client.get(root_url)
        for k in list(sec_headers.keys()):
            if r.headers.get(k):
                sec_headers[k] = "present"
    except Exception:
        pass
    missing_headers = [k for k, v in sec_headers.items() if v == "missing"]
    if missing_headers:
        findings.append(
            ScanFinding(
                category="security",
                severity="high" if len(missing_headers) >= 3 else "medium",
                title="Missing recommended HTTP security headers",
                description=f"Missing headers: {', '.join(missing_headers)}.",
                remediation="Add missing security headers at web server/CDN layer.",
                evidence=WebsiteEvidence(page_url=root_url, metadata={"missing_headers": missing_headers}),
                confidence=0.9,
            )
        )

    # TLS + HTTPS redirect
    if not tls.get("ok"):
        findings.append(
            ScanFinding(
                category="security",
                severity="high",
                title="TLS certificate or handshake issue",
                description="Unable to complete trusted TLS handshake for the site.",
                remediation="Fix TLS certificate chain/hostname/expiry and protocol configuration.",
                evidence=WebsiteEvidence(page_url=root_url, metadata=tls),
                confidence=0.88,
            )
        )
    if base_url.startswith("https://") and not _check_http_redirect(base_url):
        findings.append(
            ScanFinding(
                category="security",
                severity="medium",
                title="HTTP to HTTPS redirect not enforced",
                description="HTTP version does not consistently redirect to HTTPS.",
                remediation="Set permanent 301 redirect from HTTP to HTTPS.",
                evidence=WebsiteEvidence(page_url=base_url.replace("https://", "http://", 1)),
                confidence=0.84,
            )
        )

    # Email auth
    missing_auth = [k.upper() for k in ("spf", "dkim", "dmarc") if (dns_auth.get(k) or "") == "missing"]
    unknown_auth = [k.upper() for k in ("spf", "dkim", "dmarc") if (dns_auth.get(k) or "") == "unknown"]
    spf_record = str((dns_auth.get("records") or {}).get("spf") or "")
    if missing_auth:
        findings.append(
            ScanFinding(
                category="email_auth",
                severity="high" if "DMARC" in missing_auth else "medium",
                title=f"Email authentication gaps detected ({', '.join(missing_auth)})",
                description="Missing email-auth DNS records increase spoofing risk and hurt deliverability.",
                remediation="Publish SPF, DKIM, and DMARC records and verify alignment.",
                evidence=WebsiteEvidence(page_url=root_url, metadata=dns_auth),
                confidence=0.9,
            )
        )
    elif unknown_auth:
        findings.append(
            ScanFinding(
                category="email_auth",
                severity="low",
                title=f"Email authentication could not be fully verified ({', '.join(unknown_auth)})",
                description="DNS lookup uncertainty prevented full verification of all email-auth records in this pass.",
                remediation="Re-run DNS checks from a stable resolver and confirm SPF, DKIM selectors, and DMARC policy.",
                evidence=WebsiteEvidence(page_url=root_url, metadata=dns_auth),
                confidence=0.6,
            )
        )
    elif str(dns_auth.get("dmarc_policy") or "").lower() == "none":
        findings.append(
            ScanFinding(
                category="email_auth",
                severity="medium",
                title="DMARC policy set to none (monitoring only)",
                description="DMARC record exists but policy is p=none, which does not block spoofed messages.",
                remediation="After monitoring, move DMARC policy toward quarantine/reject.",
                evidence=WebsiteEvidence(page_url=root_url, metadata=dns_auth),
                confidence=0.86,
            )
        )
    if spf_record and "~all" in spf_record.lower():
        findings.append(
            ScanFinding(
                category="email_auth",
                severity="low",
                title="SPF uses soft-fail policy (~all)",
                description=(
                    "SPF is present and valid, but soft-fail (~all) is permissive. "
                    "It does not fail unauthorized senders as strongly as a hard-fail policy."
                ),
                remediation=(
                    "After confirming all legitimate sending services are included, consider moving SPF policy toward -all."
                ),
                evidence=WebsiteEvidence(page_url=root_url, metadata={"spf_record": spf_record[:220]}),
                confidence=0.84,
            )
        )
    # Email auth: SPF too many DNS lookups — RFC 7208 PermError risk (v35)
    _spf_domain = urlparse(root_url).netloc or root_url
    _spf_lookup_finding = _check_spf_too_many_lookups(spf_record, _spf_domain)
    if _spf_lookup_finding is not None:
        findings.append(_spf_lookup_finding)

    # Fast on-page SEO checks
    title = TITLE_RE.search(root_html)
    if not title:
        findings.append(
            ScanFinding(
                category="seo",
                severity="medium",
                title="Missing title tag on homepage",
                description="Homepage has no title tag, weakening SERP relevance and click-through.",
                remediation="Add a unique, keyword-aligned title tag.",
                evidence=WebsiteEvidence(page_url=root_url),
                confidence=0.9,
            )
        )
    meta_desc = META_DESC_RE.search(root_html)
    if not meta_desc:
        findings.append(
            ScanFinding(
                category="seo",
                severity="medium",
                title="Missing meta description on homepage",
                description="Homepage lacks a meta description, reducing snippet quality.",
                remediation="Add a compelling 120–160 character meta description.",
                evidence=WebsiteEvidence(page_url=root_url),
                confidence=0.86,
            )
        )
    h1_count = len(H1_RE.findall(root_html))
    if h1_count == 0:
        findings.append(
            ScanFinding(
                category="seo",
                severity="medium",
                title="No H1 heading found on homepage",
                description="Homepage has no H1, weakening primary topical signal.",
                remediation="Add one descriptive H1 aligned to core service intent.",
                evidence=WebsiteEvidence(page_url=root_url),
                confidence=0.88,
            )
        )

    if NOINDEX_RE.search(root_html):
        findings.append(
            ScanFinding(
                category="seo",
                severity="high",
                title="Noindex meta tag detected on homepage",
                description="Homepage appears noindexed and may be excluded from search results.",
                remediation="Remove noindex from indexable pages.",
                evidence=WebsiteEvidence(page_url=root_url),
                confidence=0.95,
            )
        )

    if not VIEWPORT_RE.search(root_html):
        findings.append(
            ScanFinding(
                category="conversion",
                severity="medium",
                title="Missing viewport meta tag",
                description="No viewport tag found; mobile rendering can degrade usability and conversion.",
                remediation="Add responsive viewport meta tag in document head.",
                evidence=WebsiteEvidence(page_url=root_url),
                confidence=0.82,
            )
        )

    # Lightweight ADA/accessibility checks (root page only, fast regex pass)
    alt_missing = len(IMG_ALT_MISSING_RE.findall(root_html))
    img_total = len(IMG_TAG_RE.findall(root_html))
    if img_total > 0 and alt_missing > 0:
        ratio = alt_missing / max(1, img_total)
        if alt_missing >= 8 or ratio >= 0.5:
            sev = "high"
        elif alt_missing >= 3 or ratio >= 0.2:
            sev = "medium"
        else:
            sev = "low"
        findings.append(
            ScanFinding(
                category="ada",
                severity=sev,
                title=f"Images missing alt text ({alt_missing} of {img_total})",
                description=(
                    "Some homepage images are missing alt text, so screen readers cannot describe key content."
                ),
                remediation="Add descriptive alt text to meaningful images and empty alt attributes for decorative images.",
                evidence=WebsiteEvidence(page_url=root_url, metadata={"img_total": img_total, "alt_missing": alt_missing}),
                confidence=0.9,
            )
        )

    if not LANG_ATTR_RE.search(root_html[:1200]):
        findings.append(
            ScanFinding(
                category="ada",
                severity="medium",
                title="HTML language attribute missing",
                description="The page does not declare a document language, which can reduce screen-reader accuracy.",
                remediation='Add a language attribute to the HTML tag, for example: <html lang="en">.',
                evidence=WebsiteEvidence(page_url=root_url),
                confidence=0.88,
            )
        )

    if not SKIP_NAV_RE.search(root_html):
        findings.append(
            ScanFinding(
                category="ada",
                severity="medium",
                title="No skip-navigation link detected",
                description=(
                    "Keyboard and assistive-technology users may need to tab through full navigation before reaching main content."
                ),
                remediation="Add a visible-on-focus 'Skip to main content' link at the top of the page.",
                evidence=WebsiteEvidence(page_url=root_url),
                confidence=0.82,
            )
        )

    form_count = len(FORM_RE.findall(root_html))
    inputs = len(INPUT_TYPE_RE.findall(root_html))
    labels = len(LABEL_RE.findall(root_html))
    if form_count > 0 and inputs > 0 and labels < inputs:
        gap = inputs - labels
        sev = "high" if gap >= 4 else "medium"
        findings.append(
            ScanFinding(
                category="ada",
                severity=sev,
                title="Form fields may be missing accessible labels",
                description=(
                    f"Detected {inputs} text/email/tel/password/search inputs but only {labels} labels on the homepage."
                ),
                remediation=(
                    "Ensure each form control has a programmatic label (<label for=...> or aria-label/aria-labelledby)."
                ),
                evidence=WebsiteEvidence(page_url=root_url, metadata={"forms": form_count, "inputs": inputs, "labels": labels}),
                confidence=0.84,
            )
        )

    # Lightweight robots/sitemap presence check
    robots = _check_robots_txt(base_url)
    if not robots.get("found"):
        findings.append(
            ScanFinding(
                category="seo",
                severity="low",
                title="robots.txt not found",
                description="No robots.txt detected, which can reduce crawl control and sitemap discoverability.",
                remediation="Publish robots.txt and reference sitemap.xml.",
                evidence=WebsiteEvidence(page_url=base_url.rstrip("/") + "/robots.txt"),
                confidence=0.8,
            )
        )

    # quick performance signal
    if load_times:
        t = max(load_times.values())
        if t > 4.0:
            findings.append(
                ScanFinding(
                    category="performance",
                    severity="medium",
                    title=f"Slow homepage response ({t:.1f}s)",
                    description="Observed slow response can hurt conversions and SEO.",
                    remediation="Enable caching/CDN and optimize assets.",
                    evidence=WebsiteEvidence(page_url=root_url, metadata={"load_s": round(t, 2)}),
                    confidence=0.8,
                )
            )

    _sanitize_findings(findings)
    for finding in findings:
        validate_finding(finding)

    return {
        "mode": "light",
        "base_url": root_url,
        "pages": [root_url],
        "screenshots": {},
        "tls": tls,
        "dns_auth": dns_auth,
        "robots": robots,
        "exposed_files": [],
        "load_times": {u: round(t, 2) for u, t in load_times.items()},
        "findings": findings,
        "finding_dicts": [_finding_to_dict(f) for f in findings],
    }


def run_scan_pipeline(*, settings: AgentSettings, website: str, out_dir: Path, mode: str = "deep") -> dict[str, Any]:
    base_url = _norm_url(website)
    parsed_base = urlparse(base_url)
    host = parsed_base.hostname or parsed_base.netloc
    tls = _tls_info(host) if host else {"ok": False, "error": "missing_host"}
    dns_auth = _email_dns(host) if host else {"spf": "unknown", "dmarc": "unknown", "dkim": "unknown", "reason": "missing_host"}
    try:
        pages, load_times = _fetch_pages(base_url)
    except Exception as exc:
        return _fallback_scan_result(
            base_url=base_url,
            out_dir=out_dir,
            reason=f"page_fetch_error:{exc}",
            tls=tls,
            dns_auth=dns_auth,
        )
    if not pages:
        return _fallback_scan_result(
            base_url=base_url,
            out_dir=out_dir,
            reason="no_pages_fetched",
            tls=tls,
            dns_auth=dns_auth,
        )

    page_urls = list(pages.keys())
    root_url = page_urls[0]
    if str(mode or "deep").lower() == "light":
        return _run_light_scan_pipeline(
            base_url=base_url,
            root_url=root_url,
            pages=pages,
            load_times=load_times,
            tls=tls,
            dns_auth=dns_auth,
            out_dir=out_dir,
        )

    shots_dir = out_dir / "screenshots"
    shot_map, browser_load_ms, axe_violations = _maybe_playwright_screenshots(page_urls, shots_dir)
    # Always ensure at least 3 valid screenshot placeholders
    if len([v for k, v in shot_map.items() if "__mobile" not in k]) < 3:
        for i, url in enumerate(page_urls[:3], start=1):
            if url in shot_map:
                continue
            pg_html = pages.get(url, "")
            title = TITLE_RE.search(pg_html)
            title_text = _clean_text(title.group(1) if title else url, max_len=120)
            shot_map[url] = _capture_placeholder_screenshot(
                shots_dir / f"page_{i}.png", title_text, _clean_text(pg_html, max_len=380)
            )

    findings: list[ScanFinding] = []

    # --- Axe-core ADA findings (real WCAG violations from browser injection) ---
    if axe_violations:
        findings.extend(_axe_violations_to_findings(axe_violations, page_urls[0], shot_map))

    # --- Browser-side performance timing (real user experience, not just HTTP) ---
    for url, ms in sorted(browser_load_ms.items(), key=lambda x: -x[1])[:2]:
        if ms > 6000:
            _browser_perf_sev = "high"
        elif ms > 3000:
            _browser_perf_sev = "medium"
        else:
            continue
        findings.append(
            ScanFinding(
                category="performance",
                severity=_browser_perf_sev,
                title=f"Browser load time {ms / 1000:.1f}s (user-facing metric)",
                description=(
                    f"The page reached DOM content loaded in {ms / 1000:.1f}s in a real browser. "
                    "Google's Core Web Vitals target is under 2.5s for Largest Contentful Paint. "
                    "A 1-second delay in load time reduces conversions by ~7% on average."
                ),
                remediation=(
                    "Enable server-side compression (gzip/brotli), defer non-critical JavaScript, "
                    "serve images in WebP format with explicit dimensions, and use a CDN (e.g. Cloudflare free tier)."
                ),
                evidence=WebsiteEvidence(
                    page_url=url,
                    screenshot_path=shot_map.get(url),
                    metadata={"browser_load_ms": ms},
                ),
                confidence=0.88,
            )
        )

    root_html = pages[root_url]
    parsed = urlparse(root_url)
    host = parsed.hostname or parsed.netloc

    # --- Security: HTTP response headers ---
    sec_headers = {
        "strict-transport-security": "missing",
        "content-security-policy": "missing",
        "x-frame-options": "missing",
        "x-content-type-options": "missing",
        "permissions-policy": "missing",
        "referrer-policy": "missing",
    }
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            r = client.get(root_url)
        for k in list(sec_headers.keys()):
            if r.headers.get(k):
                sec_headers[k] = "present"
        # v18: server technology version disclosure
        _srv_finding = _check_server_version_disclosure(dict(r.headers), root_url)
        if _srv_finding is not None:
            findings.append(_srv_finding)
        # v18: HTTP compression check (reuse this response, avoids an extra request)
        _compression_finding = _check_compression_enabled(
            root_url,
            response_headers=dict(r.headers),
            response_size_bytes=len(r.content),
        )
        if _compression_finding is not None:
            findings.append(_compression_finding)
        # v19: CSP weak directives (only fires when CSP is present but misconfigured)
        _csp_finding = _check_csp_weak_directives(dict(r.headers), root_url)
        if _csp_finding is not None:
            findings.append(_csp_finding)
        # v19: cookie security flags (HttpOnly, Secure, SameSite)
        _cookie_finding = _check_cookie_security_flags(dict(r.headers), root_url)
        if _cookie_finding is not None:
            findings.append(_cookie_finding)
        # v19: permissive CORS policy (Access-Control-Allow-Origin: *)
        _cors_finding = _check_cors_misconfiguration(dict(r.headers), root_url)
        if _cors_finding is not None:
            findings.append(_cors_finding)
        # v29: HSTS present but weakly configured (short max-age or missing includeSubDomains)
        _hsts_finding = _check_hsts_weak_directives(dict(r.headers), root_url)
        if _hsts_finding is not None:
            findings.append(_hsts_finding)
        # v29: Referrer-Policy explicitly set to an unsafe value
        _referrer_finding = _check_referrer_policy_unsafe(dict(r.headers), root_url)
        if _referrer_finding is not None:
            findings.append(_referrer_finding)
        # v30: CSP present but missing frame-ancestors directive (clickjacking gap)
        _x_frame_finding = _check_x_frame_options(dict(r.headers), root_url)
        if _x_frame_finding is not None:
            findings.append(_x_frame_finding)
        # v31: missing or non-cacheable Cache-Control header (reuses existing httpx response)
        _cache_finding = _check_cache_control_headers(dict(r.headers), root_url)
        if _cache_finding is not None:
            findings.append(_cache_finding)
        # v33: X-Content-Type-Options: nosniff missing (MIME-sniffing attack vector)
        _xcto_finding = _check_x_content_type_options(dict(r.headers), root_url)
        if _xcto_finding is not None:
            findings.append(_xcto_finding)
        # v33: Permissions-Policy header absent (unrestricted browser API access)
        _perms_finding = _check_permissions_policy(dict(r.headers), root_url)
        if _perms_finding is not None:
            findings.append(_perms_finding)
        # v34: session/auth cookies missing __Secure- prefix — cookie injection risk
        _cookie_prefix_finding = _check_cookie_prefix_security(dict(r.headers), root_url)
        if _cookie_prefix_finding is not None:
            findings.append(_cookie_prefix_finding)
    except Exception:
        pass

    missing_headers = [k for k, v in sec_headers.items() if v == "missing"]
    if missing_headers:
        severity = "high" if len(missing_headers) >= 4 else "medium"
        findings.append(
            ScanFinding(
                category="security",
                severity=severity,
                title="Missing recommended HTTP security headers",
                description=(
                    f"The site is missing {len(missing_headers)} security headers: "
                    f"{', '.join(missing_headers)}. "
                    "Without these, the site is vulnerable to clickjacking, MIME sniffing, "
                    "and cross-site scripting attacks."
                ),
                remediation=(
                    "Add all missing headers at the web server or CDN layer (nginx, Apache, Cloudflare). "
                    "Strict-Transport-Security and X-Content-Type-Options are the highest priority."
                ),
                evidence=WebsiteEvidence(page_url=root_url, screenshot_path=shot_map.get(root_url), headers=sec_headers),
                confidence=0.95,
            )
        )

    # --- Security: TLS ---
    if not tls.get("ok"):
        findings.append(
            ScanFinding(
                category="security",
                severity="high",
                title="TLS certificate or handshake issue",
                description=(
                    f"Unable to establish a trusted TLS connection to {host}. "
                    "Visitors may see browser security warnings."
                ),
                remediation="Review certificate chain, expiry, hostname coverage, and TLS config. Use Let's Encrypt for free certificates.",
                evidence=WebsiteEvidence(page_url=root_url, screenshot_path=shot_map.get(root_url), metadata=tls),
                confidence=0.88,
            )
        )
    elif tls.get("protocol") and "TLSv1." in str(tls.get("protocol")):
        proto = str(tls.get("protocol"))
        if proto in {"TLSv1", "TLSv1.1"}:
            findings.append(
                ScanFinding(
                    category="security",
                    severity="high",
                    title=f"Outdated TLS protocol in use ({proto})",
                    description=f"{proto} is deprecated and no longer considered secure by modern browsers.",
                    remediation="Upgrade to TLS 1.2 minimum; TLS 1.3 preferred. Disable legacy protocols in your server config.",
                    evidence=WebsiteEvidence(page_url=root_url, metadata=tls),
                    confidence=0.90,
                )
            )

    # --- Security: SSL cert expiry warning ---
    if tls.get("ok"):
        days_left = _ssl_cert_expiry_days(tls)
        if days_left is not None and days_left < 60:
            sev_cert = "critical" if days_left < 14 else "high" if days_left < 30 else "medium"
            findings.append(
                ScanFinding(
                    category="security",
                    severity=sev_cert,
                    title=f"SSL certificate expiring soon ({days_left} days remaining)",
                    description=(
                        f"The TLS/SSL certificate for {host} expires in approximately {days_left} days. "
                        "Once expired, all visitors will see a full-screen security warning that blocks access, "
                        "causing immediate traffic loss and lasting customer trust damage."
                    ),
                    remediation=(
                        "Renew your SSL certificate before it expires. "
                        "If using Let's Encrypt, verify auto-renewal is configured with 'certbot renew --dry-run'. "
                        "Most hosting control panels (cPanel, Plesk) offer one-click certificate renewal."
                    ),
                    evidence=WebsiteEvidence(
                        page_url=root_url,
                        metadata={"days_to_expiry": days_left, "not_after": tls.get("not_after")},
                    ),
                    confidence=0.97,
                )
            )

    # --- Security: HTTP→HTTPS redirect ---
    if base_url.startswith("https://"):
        redirects = _check_http_redirect(base_url)
        if not redirects:
            findings.append(
                ScanFinding(
                    category="security",
                    severity="medium",
                    title="HTTP to HTTPS redirect not enforced",
                    description="Visitors accessing the site via http:// are not automatically redirected to the secure version.",
                    remediation="Configure a permanent 301 redirect from http:// to https:// at the server or CDN level.",
                    evidence=WebsiteEvidence(page_url=base_url.replace("https://", "http://", 1)),
                    confidence=0.85,
                )
            )

    # --- Security: Mixed content ---
    _mixed_pages: list[tuple[str, int]] = []
    for url, pg_html in pages.items():
        mixed_count = len(HTTP_SRC_RE.findall(pg_html))
        if mixed_count > 0:
            _mixed_pages.append((url, mixed_count))
    if _mixed_pages:
        _first_url, _first_count = _mixed_pages[0]
        _total_mixed = sum(c for _, c in _mixed_pages)
        _page_count = len(_mixed_pages)
        _affected_str = ", ".join(u for u, _ in _mixed_pages[:3])
        if _page_count > 3:
            _affected_str += f" (+ {_page_count - 3} more page(s))"
        _desc = (
            f"{_total_mixed} HTTP resource(s) found across {_page_count} HTTPS page(s). "
            f"Browsers may block these, breaking images or scripts. "
            f"Affected pages: {_affected_str}"
        )
        findings.append(
            ScanFinding(
                category="security",
                severity="high" if _page_count >= 3 else "medium",
                title="Mixed content (HTTP resources on HTTPS page)",
                description=_desc,
                remediation="Update all embedded resource URLs to use https:// or protocol-relative URLs (//).",
                evidence=WebsiteEvidence(
                    page_url=_first_url,
                    screenshot_path=shot_map.get(_first_url),
                    snippet=_affected_str,
                    metadata={"mixed_count": _total_mixed, "pages_affected": _page_count},
                ),
                confidence=0.80,
            )
        )

    # --- Security: CMS detection ---
    cms_info = _detect_cms(root_html)
    if cms_info and cms_info.get("cms") in ("WordPress", "Joomla", "Drupal"):
        cms_name = cms_info["cms"]
        version_str = str(cms_info.get("version", ""))[:80]
        findings.append(
            ScanFinding(
                category="security",
                severity="medium",
                title=f"{cms_name} CMS detected — security hardening and updates required",
                description=(
                    f"A {cms_name} installation was detected ({version_str}). "
                    f"{cms_name} is a frequent target for automated attacks. "
                    "Outdated core files, plugins, and themes are the #1 cause of SMB website compromise, "
                    "leading to data theft, spam injection, and Google blacklisting."
                ),
                remediation=(
                    f"Ensure {cms_name} core, all plugins, and themes are on the latest versions. "
                    "Enable automatic security updates. Install a security plugin (Wordfence or Sucuri for WordPress). "
                    "Use strong admin passwords and consider enabling two-factor authentication."
                ),
                evidence=WebsiteEvidence(page_url=root_url, metadata=cms_info),
                confidence=0.83,
            )
        )

    # --- Email authentication ---
    for key in ("spf", "dmarc", "dkim"):
        if dns_auth.get(key) == "missing":
            severity = "high" if key == "dmarc" else "medium"
            findings.append(
                ScanFinding(
                    category="email_auth",
                    severity=severity,
                    title=f"{key.upper()} record missing",
                    description=(
                        f"{key.upper()} does not appear to be published for {host}. "
                        f"{'Without DMARC, anyone can spoof emails from your domain.' if key == 'dmarc' else ''}"
                        f"{'Without SPF, email servers cannot verify your sending identity.' if key == 'spf' else ''}"
                        f"{'Without DKIM, email signatures cannot be verified.' if key == 'dkim' else ''}"
                    ).strip(),
                    remediation=f"Publish and validate a {key.upper()} DNS record. Use mxtoolbox.com to verify after publishing.",
                    evidence=WebsiteEvidence(page_url=root_url, metadata=dns_auth),
                    confidence=0.88,
                )
            )
        elif dns_auth.get(key) == "unknown":
            findings.append(
                ScanFinding(
                    category="email_auth",
                    severity="low",
                    title=f"{key.upper()} status could not be verified",
                    description=(
                        f"{key.upper()} lookup for {host} was inconclusive from this environment. "
                        "This does not confirm the record is missing."
                    ),
                    remediation=f"Re-check {key.upper()} from a stable DNS resolver and validate from live email headers.",
                    evidence=WebsiteEvidence(page_url=root_url, metadata=dns_auth),
                    confidence=0.6,
                )
            )

    # DMARC present but policy=none is a weak config
    if dns_auth.get("dmarc") == "present" and dns_auth.get("dmarc_policy") == "none":
        findings.append(
            ScanFinding(
                category="email_auth",
                severity="medium",
                title="DMARC policy is set to 'none' (monitoring only)",
                description="A DMARC p=none policy does not block spoofed emails. It only sends reports but does not protect the domain.",
                remediation="Upgrade DMARC policy to p=quarantine or p=reject after reviewing DMARC reports for legitimate sending sources.",
                evidence=WebsiteEvidence(page_url=root_url, metadata={"dmarc_record": dns_auth.get("records", {}).get("dmarc")}),
                confidence=0.82,
            )
        )

    # --- Robots.txt and indexing ---
    robots = _check_robots_txt(base_url)
    if not robots["found"]:
        findings.append(
            ScanFinding(
                category="seo",
                severity="low",
                title="robots.txt not found",
                description="No robots.txt file was detected. Search engines use robots.txt to understand crawl directives and find sitemap references.",
                remediation="Create a robots.txt at the root domain. Include a Sitemap: directive pointing to your XML sitemap.",
                evidence=WebsiteEvidence(page_url=base_url.rstrip("/") + "/robots.txt"),
                confidence=0.85,
            )
        )
    elif robots["disallow_all"]:
        findings.append(
            ScanFinding(
                category="seo",
                severity="high",
                title="robots.txt is blocking all search engine crawlers",
                description="The robots.txt file contains 'Disallow: /' for all user agents, preventing all pages from being indexed.",
                remediation="Review and correct robots.txt to allow search engines to crawl the site. Remove or narrow 'Disallow: /'.",
                evidence=WebsiteEvidence(page_url=base_url.rstrip("/") + "/robots.txt", snippet=robots.get("raw", "")[:200]),
                confidence=0.93,
            )
        )
    elif not robots["has_sitemap"]:
        findings.append(
            ScanFinding(
                category="seo",
                severity="low",
                title="robots.txt missing Sitemap declaration",
                description="robots.txt exists but does not reference an XML sitemap. Sitemap declarations speed up search engine discovery.",
                remediation="Add a 'Sitemap: https://yourdomain.com/sitemap.xml' line to robots.txt after creating or verifying your XML sitemap.",
                evidence=WebsiteEvidence(page_url=base_url.rstrip("/") + "/robots.txt"),
                confidence=0.78,
            )
        )

    # --- SEO: XML sitemap existence ---
    # Only check if robots.txt doesn't already reference a sitemap (avoid redundant finding)
    if not robots.get("has_sitemap"):
        _sitemap_found = False
        for _smap_url in [
            base_url.rstrip("/") + "/sitemap.xml",
            base_url.rstrip("/") + "/sitemap_index.xml",
        ]:
            try:
                with httpx.Client(timeout=7.0, follow_redirects=True) as _sc:
                    _sr = _sc.get(_smap_url)
                    if _sr.status_code == 200 and (
                        "xml" in _sr.headers.get("content-type", "").lower()
                        or "<urlset" in _sr.text[:1000]
                        or "<sitemapindex" in _sr.text[:1000]
                    ):
                        _sitemap_found = True
                        break
            except Exception:
                pass
        if not _sitemap_found:
            findings.append(
                ScanFinding(
                    category="seo",
                    severity="medium",
                    title="XML sitemap not found",
                    description=(
                        "No XML sitemap was found at /sitemap.xml or /sitemap_index.xml, and robots.txt does not reference one. "
                        "Sitemaps accelerate search engine discovery and are especially important for new or recently updated content."
                    ),
                    remediation=(
                        "Generate an XML sitemap (Yoast SEO for WordPress, Squarespace has it built-in, etc.). "
                        "Submit the sitemap to Google Search Console for faster indexing. "
                        "Reference it in robots.txt with 'Sitemap: https://yourdomain.com/sitemap.xml'."
                    ),
                    evidence=WebsiteEvidence(page_url=base_url.rstrip("/") + "/sitemap.xml"),
                    confidence=0.86,
                )
            )

    # --- Exposed sensitive files ---
    exposed_hits = _check_exposed_files(base_url)
    confirmed_exposed = [h for h in exposed_hits if int(h.get("status_code") or 0) == 200]
    guarded_paths = [h for h in exposed_hits if int(h.get("status_code") or 0) in {401, 403}]
    if confirmed_exposed:
        exposed_paths = [str(h.get("path") or "") for h in confirmed_exposed]
        findings.append(
            ScanFinding(
                category="security",
                severity="critical" if "/.env" in exposed_paths or "/.git/HEAD" in exposed_paths else "high",
                title=f"Sensitive path(s) publicly accessible ({len(confirmed_exposed)} found)",
                description=(
                    f"The following paths returned HTTP 200 responses: {', '.join(exposed_paths[:6])}. "
                    "Exposed configuration files or admin panels are a critical security risk."
                ),
                remediation=(
                    "Immediately restrict access to sensitive paths via server configuration or firewall rules. "
                    "For /.env or /.git/HEAD, deny access via nginx/Apache and audit for credential exposure."
                ),
                evidence=WebsiteEvidence(
                    page_url=base_url,
                    metadata={
                        "exposed_paths": exposed_hits,
                    },
                ),
                confidence=0.92,
            )
        )
    elif guarded_paths:
        guarded_list = [str(h.get("path") or "") for h in guarded_paths]
        findings.append(
            ScanFinding(
                category="security",
                severity="low",
                title=f"Sensitive paths are access-controlled ({len(guarded_paths)} checked)",
                description=(
                    f"Sensitive endpoints returned HTTP 401/403 (for example: {', '.join(guarded_list[:4])}). "
                    "This indicates access controls are active for those paths."
                ),
                remediation=(
                    "Keep these controls in place and confirm they return 404 in public environments if possible."
                ),
                evidence=WebsiteEvidence(page_url=base_url, metadata={"protected_paths": guarded_paths}),
                confidence=0.9,
            )
        )

    # --- Root-level homepage checks: analytics and lang ---
    if not ANALYTICS_RE.search(root_html):
        findings.append(
            ScanFinding(
                category="conversion",
                severity="medium",
                title="No web analytics detected on homepage",
                description="No common analytics tag (Google Analytics, Meta Pixel, etc.) was detected. Without tracking, there is no visibility into visitor behavior.",
                remediation="Install Google Analytics 4 (free) and set up conversion goals for form submissions and phone clicks.",
                evidence=WebsiteEvidence(page_url=root_url),
                confidence=0.75,
            )
        )

    if not LANG_ATTR_RE.search(root_html[:500]):
        findings.append(
            ScanFinding(
                category="ada",
                severity="low",
                title="HTML lang attribute missing",
                description="The <html> element does not declare a language. Screen readers rely on this to select the correct speech engine.",
                remediation='Add lang="en" (or your content language) to the opening <html> tag.',
                evidence=WebsiteEvidence(page_url=root_url),
                confidence=0.88,
            )
        )

    if not FAVICON_RE.search(root_html[:2000]):
        findings.append(
            ScanFinding(
                category="conversion",
                severity="low",
                title="Favicon missing or not declared",
                description="No favicon link tag was found. Favicons reinforce brand recognition in browser tabs and bookmark lists.",
                remediation="Create a 32x32 and 192x192 favicon, upload them, and add <link rel='icon' href='/favicon.ico'> to your <head>.",
                evidence=WebsiteEvidence(page_url=root_url),
                confidence=0.80,
            )
        )

    if not SOCIAL_LINK_RE.search(root_html):
        findings.append(
            ScanFinding(
                category="conversion",
                severity="low",
                title="No social media profile links found",
                description="No links to Facebook, LinkedIn, Instagram, or Yelp were detected. Social proof links build trust and support local SEO.",
                remediation="Add links to your active social profiles in the site footer. Keep profiles current with reviews and posts.",
                evidence=WebsiteEvidence(page_url=root_url),
                confidence=0.72,
            )
        )

    # --- Conversion: Live chat widget opportunity ---
    if not CHAT_WIDGET_RE.search(root_html):
        findings.append(
            ScanFinding(
                category="conversion",
                severity="low",
                title="No live chat widget detected",
                description=(
                    "No live chat tool (Intercom, Drift, Tawk.to, HubSpot Chat, etc.) was found on the homepage. "
                    "Live chat can engage visitors who browse but hesitate to submit a contact form — "
                    "particularly for time-sensitive or higher-ticket service inquiries. "
                    "Many local service businesses miss leads outside business hours due to the lack of a chat capture fallback."
                ),
                remediation=(
                    "Install a free live chat widget such as Tawk.to or HubSpot's free chat tool. "
                    "Configure offline hours to redirect to email capture so no inquiry is lost. "
                    "Even a simple chat-to-email bridge converts hesitant visitors who would otherwise leave silently."
                ),
                evidence=WebsiteEvidence(page_url=root_url),
                confidence=0.65,
            )
        )

    # --- Compliance/Conversion: Cookie consent / privacy notice ---
    if not COOKIE_CONSENT_RE.search(root_html):
        findings.append(
            ScanFinding(
                category="conversion",
                severity="medium",
                title="No cookie consent or privacy notice detected",
                description=(
                    "No cookie consent mechanism (OneTrust, CookieYes, or GDPR/CCPA banner) was detected on the homepage. "
                    "Businesses collecting visitor data via analytics or contact forms may face regulatory exposure "
                    "under GDPR (EU visitors) and CCPA (California visitors). A missing privacy notice also reduces visitor trust."
                ),
                remediation=(
                    "Implement a lightweight cookie consent solution (CookieYes free tier or Cookiebot). "
                    "At minimum, add a privacy policy linked from the footer and disclose what data is collected "
                    "and how it is used. Ensure analytics only fires after consent is granted."
                ),
                evidence=WebsiteEvidence(page_url=root_url),
                confidence=0.72,
            )
        )

    # --- Conversion: No video content on homepage ---
    if not VIDEO_EMBED_RE.search(root_html):
        findings.append(
            ScanFinding(
                category="conversion",
                severity="low",
                title="No video content detected on homepage",
                description=(
                    "No embedded video (YouTube, Vimeo, etc.) was found on the homepage. "
                    "Homepage explainer videos and testimonial clips consistently reduce visitor hesitation "
                    "and improve time-on-page — both important signals for service businesses where trust is the "
                    "primary conversion barrier before a prospect picks up the phone or submits a form."
                ),
                remediation=(
                    "Add a 60–90 second explainer or testimonial video to the homepage, ideally above the fold or "
                    "near the primary CTA. Host on YouTube (free) and embed the iframe player. "
                    "Even a smartphone-recorded walkthrough of your work adds credibility. Include captions for accessibility."
                ),
                evidence=WebsiteEvidence(page_url=root_url),
                confidence=0.60,
            )
        )

    # --- SEO: Open Graph / social sharing tags ---
    has_og_title = bool(OG_TITLE_RE.search(root_html[:6000]))
    has_og_image = bool(OG_IMAGE_RE.search(root_html[:6000]))
    if not has_og_title or not has_og_image:
        missing_og = [t for t, present in [("og:title", has_og_title), ("og:image", has_og_image)] if not present]
        findings.append(
            ScanFinding(
                category="seo",
                severity="low",
                title="Open Graph social sharing tags missing or incomplete",
                description=(
                    f"Missing Open Graph meta tags: {', '.join(missing_og)}. "
                    "When pages are shared on Facebook, LinkedIn, or Slack without OG tags, "
                    "the link preview shows a blank image and auto-generated title — measurably reducing social click-through rates."
                ),
                remediation=(
                    "Add og:title, og:description, og:image, and og:url to each page's <head> section. "
                    "Use a 1200×630 pixel branded image for og:image. "
                    "WordPress users: install Yoast SEO or RankMath to automate OG tag generation."
                ),
                evidence=WebsiteEvidence(
                    page_url=root_url,
                    metadata={"og_title_present": has_og_title, "og_image_present": has_og_image},
                ),
                confidence=0.88,
            )
        )

    # --- SEO: No Google Maps embed for local business ---
    # Only fires when LocalBusiness schema is present (confirms this is a local service business)
    has_local_schema = bool(LOCAL_BUSINESS_SCHEMA_RE.search(root_html))
    if has_local_schema and not GOOGLE_MAPS_EMBED_RE.search(root_html):
        findings.append(
            ScanFinding(
                category="seo",
                severity="low",
                title="No Google Maps embed detected for local business",
                description=(
                    "LocalBusiness structured data was found, but no Google Maps embed was detected on the site. "
                    "For local service businesses, an embedded Google Maps widget on the homepage or contact page "
                    "reinforces physical presence signals, helps visitors get directions without leaving the site, "
                    "and strengthens the link between the website and the Google Business Profile."
                ),
                remediation=(
                    "Add a Google Maps embed to your contact page: go to Google Maps, search your business location, "
                    "click Share → Embed a map, and paste the iframe code. This directly connects your site to your "
                    "Google Business Profile — a measurable local SEO signal. Ensure your GBP listing is claimed and "
                    "the address matches exactly what is in your LocalBusiness schema."
                ),
                evidence=WebsiteEvidence(page_url=root_url),
                confidence=0.70,
            )
        )

    # --- Conversion: Contact link presence ---
    if not CONTACT_LINK_RE.search(root_html):
        findings.append(
            ScanFinding(
                category="conversion",
                severity="medium",
                title="No contact page link found on homepage",
                description=(
                    "The homepage does not appear to link to a contact page. "
                    "Visitors looking to reach out may abandon the site rather than hunt for contact information."
                ),
                remediation=(
                    "Add a clearly labeled 'Contact' or 'Contact Us' link in the main navigation and footer. "
                    "If contact info is already on the homepage, also add a standalone /contact page for direct linking."
                ),
                evidence=WebsiteEvidence(page_url=root_url),
                confidence=0.74,
            )
        )

    # --- Conversion: Pricing transparency ---
    if not PRICING_KEYWORD_RE.search(_clean_text(root_html, max_len=10000)):
        findings.append(
            ScanFinding(
                category="conversion",
                severity="low",
                title="No pricing or rates information visible",
                description=(
                    "No pricing, rates, or package information was found on the homepage. "
                    "Studies show 63% of B2C buyers want to see pricing before contacting a vendor. "
                    "Without any price anchoring, visitors self-qualify out rather than requesting a quote."
                ),
                remediation=(
                    "Add a pricing or rates page with at least a starting price or range. "
                    "Even a 'Pricing starts from $X' or 'Request a free estimate' CTA with a visible value signal "
                    "boosts lead form conversions by 20–35%."
                ),
                evidence=WebsiteEvidence(page_url=root_url),
                confidence=0.68,
            )
        )

    # --- Performance: Images not using lazy loading ---
    imgs_on_root = IMG_TAG_RE.findall(root_html)
    if len(imgs_on_root) >= 3 and not LAZY_LOAD_RE.search(root_html):
        findings.append(
            ScanFinding(
                category="performance",
                severity="low",
                title=f"Images not using lazy loading ({len(imgs_on_root)} images detected)",
                description=(
                    f"Found {len(imgs_on_root)} <img> tags without lazy loading attributes. "
                    "Lazy loading defers off-screen image downloads until the user scrolls to them, "
                    "improving initial page load by 20–40% — especially on mobile connections."
                ),
                remediation=(
                    'Add loading="lazy" to all <img> tags not in the above-the-fold viewport. '
                    "Most modern CMS platforms (WordPress 5.5+, Squarespace) support this natively. "
                    "Retain loading=eager or omit for hero/above-fold images."
                ),
                evidence=WebsiteEvidence(page_url=root_url, metadata={"image_count": len(imgs_on_root)}),
                confidence=0.78,
            )
        )

    # --- ADA: Missing skip navigation link ---
    if not SKIP_NAV_RE.search(root_html[:3000]):
        findings.append(
            ScanFinding(
                category="ada",
                severity="low",
                title="Skip navigation link not detected",
                description=(
                    "No 'Skip to main content' link was found at the top of the page. "
                    "WCAG 2.4.1 (Level A) requires a skip mechanism so keyboard-only and screen reader users "
                    "can bypass repeated navigation blocks without tabbing through all menu items on every page."
                ),
                remediation=(
                    "Add a visually-hidden skip link as the very first focusable element: "
                    '<a href="#main-content" class="skip-link">Skip to main content</a>. '
                    "Use CSS to show it only on :focus. Add id='main-content' to the <main> element."
                ),
                evidence=WebsiteEvidence(page_url=root_url),
                confidence=0.76,
            )
        )

    # --- Conversion: Stale copyright year in footer ---
    _stale_year = _check_copyright_staleness(root_html)
    if _stale_year is not None:
        findings.append(
            ScanFinding(
                category="conversion",
                severity="low",
                title=f"Footer copyright year appears outdated ({_stale_year})",
                description=(
                    f"The footer displays a copyright year of {_stale_year}. "
                    "An outdated copyright year signals to visitors that the website may not be actively maintained — "
                    "a trust and credibility concern for service businesses where currency and reliability are key buying signals. "
                    "Visitors often check the copyright year as a quick proxy for whether the business is still operating."
                ),
                remediation=(
                    f"Update the copyright notice from {_stale_year} to the current year, or use a date range (e.g., © 2020–2026). "
                    "To keep it automatically current, replace the static year with JavaScript: "
                    "<script>document.write(new Date().getFullYear())</script>. "
                    "Also audit the full footer for stale hours, outdated addresses, and inactive social links."
                ),
                evidence=WebsiteEvidence(page_url=root_url, metadata={"copyright_year_found": _stale_year}),
                confidence=0.78,
            )
        )

    # --- Conversion: Custom 404 error page ---
    if not _has_custom_404(base_url):
        findings.append(
            ScanFinding(
                category="conversion",
                severity="low",
                title="No helpful custom 404 error page detected",
                description=(
                    "Broken or mistyped URLs likely show a generic server error page. "
                    "A branded 404 page with navigation links and a search box retains visitors "
                    "who hit dead links from social posts, old bookmarks, or typos — rather than bouncing."
                ),
                remediation=(
                    "Create a custom 404 page matching your site brand with: "
                    "a helpful message, primary navigation links, a site search box, and a 'Go to homepage' button. "
                    "Most CMS platforms (WordPress, Squarespace, Webflow) have built-in 404 template support."
                ),
                evidence=WebsiteEvidence(page_url=base_url.rstrip("/") + "/__check404_xyzabc_missing__"),
                confidence=0.72,
            )
        )

    # --- Per-page checks: SEO, ADA, Conversion ---
    for url, pg_html in pages.items():
        title = TITLE_RE.search(pg_html)
        title_text = _clean_text(title.group(1) if title else "")
        desc = META_DESC_RE.search(pg_html)
        desc_text = _clean_text(desc.group(1) if desc else "")
        h1_count = len(H1_RE.findall(pg_html))
        h2_count = len(H2_RE.findall(pg_html))
        h3_count = len(H3_RE.findall(pg_html))
        alt_missing = len(IMG_ALT_MISSING_RE.findall(pg_html))
        has_form = bool(FORM_RE.search(pg_html))
        has_cta = bool(CTA_RE.search(_clean_text(pg_html, max_len=6000)))
        has_schema = bool(SCHEMA_RE.search(pg_html))
        has_noindex = bool(NOINDEX_RE.search(pg_html))
        has_viewport = bool(VIEWPORT_RE.search(pg_html))
        has_phone = bool(PHONE_RE.search(_clean_text(pg_html, max_len=5000)))
        has_testimonials = bool(TESTIMONIAL_RE.search(_clean_text(pg_html, max_len=8000)))

        # SEO: Title
        if not title_text or len(title_text) < 20:
            findings.append(
                ScanFinding(
                    category="seo",
                    severity="medium",
                    title="Page title is missing or too short",
                    description=f"A concise, keyword-aligned title is required. Found: '{title_text or 'none'}'.",
                    remediation="Write a unique 45–60 character title aligned with the page's primary service keyword and location.",
                    evidence=WebsiteEvidence(page_url=url, screenshot_path=shot_map.get(url), snippet=title_text),
                    confidence=0.93,
                )
            )
        elif len(title_text) > 65:
            findings.append(
                ScanFinding(
                    category="seo",
                    severity="low",
                    title="Page title may be truncated in search results",
                    description=f"Title is {len(title_text)} characters; Google typically shows ~60. Found: '{title_text[:70]}'.",
                    remediation="Trim title to 50–60 characters to avoid truncation in SERP snippets.",
                    evidence=WebsiteEvidence(page_url=url, snippet=title_text[:100]),
                    confidence=0.82,
                )
            )

        # SEO: Meta description
        if not desc_text or len(desc_text) < 60:
            findings.append(
                ScanFinding(
                    category="seo",
                    severity="low",
                    title="Meta description missing or too thin",
                    description="The page lacks a useful meta description. Search engines may generate one automatically from page content, often poorly.",
                    remediation="Write a 140–160 character meta description focused on the page's service, location, and a clear benefit statement.",
                    evidence=WebsiteEvidence(page_url=url, screenshot_path=shot_map.get(url), snippet=desc_text),
                    confidence=0.88,
                )
            )

        # SEO: H1 structure
        if h1_count == 0:
            findings.append(
                ScanFinding(
                    category="seo",
                    severity="medium",
                    title="No H1 heading found",
                    description="This page has no H1 tag. Search engines use H1 as the primary on-page topic signal.",
                    remediation="Add one clear H1 per page that matches the primary keyword intent for that page.",
                    evidence=WebsiteEvidence(page_url=url, metadata={"h1_count": h1_count}),
                    confidence=0.90,
                )
            )
        elif h1_count > 1:
            findings.append(
                ScanFinding(
                    category="seo",
                    severity="low",
                    title="Multiple H1 headings detected",
                    description=f"Found {h1_count} H1 tags. Multiple H1s dilute the primary topic signal and can confuse search engines.",
                    remediation="Keep exactly one H1 per page; use H2 and H3 for subsections.",
                    evidence=WebsiteEvidence(page_url=url, metadata={"h1_count": h1_count}),
                    confidence=0.84,
                )
            )

        # SEO: Generic / weak H1 keyword quality
        _generic_h1 = _check_generic_h1(pg_html)
        if _generic_h1 is not None:
            findings.append(
                ScanFinding(
                    category="seo",
                    severity="medium",
                    title="H1 heading uses generic or low-value text",
                    description=(
                        f"The H1 heading contains '{_generic_h1[:60]}', which is generic and provides "
                        "no keyword signal. Search engines use H1 as the primary on-page topic indicator. "
                        "A vague H1 fails to communicate your service or location to Google."
                    ),
                    remediation=(
                        "Replace with a specific, keyword-rich heading describing your primary service "
                        "and location. Example: 'Licensed Plumber in Austin, TX — 24/7 Emergency Service' "
                        "instead of 'Welcome' or 'Home'. This is one of the highest-ROI on-page SEO fixes."
                    ),
                    evidence=WebsiteEvidence(page_url=url, snippet=_generic_h1[:100]),
                    confidence=0.85,
                )
            )

        # SEO: Heading hierarchy (H3 without H2 = skipped level)
        _hier = _check_heading_hierarchy(pg_html)
        if _hier is not None:
            findings.append(
                ScanFinding(
                    category="seo",
                    severity="low",
                    title="Heading hierarchy skips H2 level",
                    description=(
                        f"This page has {_hier['h1']} H1 and {_hier['h3']} H3 heading(s) but no H2 headings. "
                        "Skipping heading levels breaks the document outline, weakens on-page SEO signals, "
                        "and impairs screen reader navigation."
                    ),
                    remediation=(
                        "Structure headings sequentially: H1 (page title) → H2 (major sections) → H3 (subsections). "
                        "Never skip heading levels. Use H2 for main service areas or content topics, "
                        "H3 for specifics within those sections."
                    ),
                    evidence=WebsiteEvidence(page_url=url, metadata=_hier),
                    confidence=0.80,
                )
            )

        # SEO/Conversion: Thin homepage content
        if url == root_url:
            _hp_thin = _check_homepage_thin_content(pg_html)
            if _hp_thin is not None:
                findings.append(
                    ScanFinding(
                        category="seo",
                        severity="medium",
                        title=f"Homepage content is thin ({_hp_thin} words)",
                        description=(
                            f"The homepage contains approximately {_hp_thin} words of readable content. "
                            "A strong homepage should include 300–500+ words covering your primary service, "
                            "location signals, trust factors, and a clear value proposition. "
                            "Thin homepages rank poorly for competitive local service keywords."
                        ),
                        remediation=(
                            "Expand homepage content to 400+ words: add a clear service description with your city/area, "
                            "2–3 differentiating value propositions, a brief 'About' intro, and customer testimonials. "
                            "Each paragraph should naturally use your primary and secondary service keywords."
                        ),
                        evidence=WebsiteEvidence(
                            page_url=url, metadata={"word_count": _hp_thin}
                        ),
                        confidence=0.80,
                    )
                )

        # SEO: Schema markup
        if not has_schema and url == root_url:
            findings.append(
                ScanFinding(
                    category="seo",
                    severity="low",
                    title="No structured data (Schema.org) markup detected",
                    description="Schema markup helps search engines display rich results (ratings, hours, location). Missing for this page.",
                    remediation="Add LocalBusiness or Service schema markup using JSON-LD. Google's Rich Results Test can validate it.",
                    evidence=WebsiteEvidence(page_url=url),
                    confidence=0.78,
                )
            )

        # SEO: noindex
        if has_noindex:
            findings.append(
                ScanFinding(
                    category="seo",
                    severity="high",
                    title="Noindex meta tag detected — page excluded from search",
                    description="This page has a robots noindex directive. It will not appear in Google search results.",
                    remediation="Remove the noindex meta tag if you want this page indexed. If intentional, document the reason.",
                    evidence=WebsiteEvidence(page_url=url),
                    confidence=0.95,
                )
            )

        # ADA: alt text
        if alt_missing > 0:
            findings.append(
                ScanFinding(
                    category="ada",
                    severity="medium",
                    title=f"Images missing alt text ({alt_missing} found)",
                    description=f"{alt_missing} image(s) lack alt attributes. Screen readers cannot describe these images to visually impaired users.",
                    remediation='Add descriptive alt text to all informative images. Use alt="" (empty) for purely decorative images.',
                    evidence=WebsiteEvidence(page_url=url, screenshot_path=shot_map.get(url), metadata={"alt_missing": alt_missing}),
                    confidence=0.88,
                )
            )

        # ADA: mobile viewport
        if not has_viewport and url == root_url:
            findings.append(
                ScanFinding(
                    category="ada",
                    severity="medium",
                    title="Mobile viewport meta tag missing",
                    description="Without a viewport meta tag, mobile users see a zoomed-out desktop layout. This is also an ADA accessibility concern.",
                    remediation='Add <meta name="viewport" content="width=device-width, initial-scale=1"> to the <head> section.',
                    evidence=WebsiteEvidence(page_url=url),
                    confidence=0.85,
                )
            )

        # ADA: ARIA main landmark (v15)
        if url == root_url:
            _aria_finding = _check_aria_landmarks(pg_html, url)
            if _aria_finding is not None:
                findings.append(_aria_finding)

        # Performance: render-blocking scripts (v15)
        if url == root_url:
            _rbs_finding = _check_render_blocking_scripts(pg_html, url)
            if _rbs_finding is not None:
                findings.append(_rbs_finding)

        # Performance: images missing explicit dimensions / CLS risk (v15)
        if url == root_url:
            _dims_finding = _check_image_dimensions(pg_html, url)
            if _dims_finding is not None:
                findings.append(_dims_finding)

        # SEO: multiple H1 tags per page (v16)
        _multi_h1_finding = _check_multiple_h1s(pg_html, url)
        if _multi_h1_finding is not None:
            findings.append(_multi_h1_finding)

        # Conversion: no social proof on homepage (v16)
        if url == root_url:
            _social_finding = _check_social_proof_absence(pg_html, url)
            if _social_finding is not None:
                findings.append(_social_finding)

        # Performance: Google Fonts without preconnect hint (v16)
        if url == root_url:
            _preconnect_finding = _check_preconnect_hints(pg_html, url)
            if _preconnect_finding is not None:
                findings.append(_preconnect_finding)

        # Security: outdated jQuery version (v17)
        _jquery_finding = _check_jquery_outdated(pg_html, url)
        if _jquery_finding is not None:
            findings.append(_jquery_finding)

        # Performance: excessive third-party scripts (v17)
        _tps_finding = _check_third_party_scripts(pg_html, url)
        if _tps_finding is not None:
            findings.append(_tps_finding)

        # ADA: iframes without title attribute (v17)
        _iframe_finding = _check_iframes_without_title(pg_html, url)
        if _iframe_finding is not None:
            findings.append(_iframe_finding)

        # Security: external scripts missing SRI (v18)
        _sri_finding = _check_sri_missing(pg_html, url)
        if _sri_finding is not None:
            findings.append(_sri_finding)

        # Conversion: CTA
        if not has_cta:
            findings.append(
                ScanFinding(
                    category="conversion",
                    severity="medium",
                    title="No clear call-to-action detected",
                    description="No strong action phrase (e.g., 'Book now', 'Get a quote', 'Contact us') was found. Visitors may leave without taking action.",
                    remediation="Add a prominent CTA above the fold and repeat near testimonials/proof sections. Use action-oriented language.",
                    evidence=WebsiteEvidence(page_url=url, screenshot_path=shot_map.get(url)),
                    confidence=0.78,
                )
            )

        # Conversion: form without trust cues
        if has_form and not re.search(r"(privacy|consent|terms|secure)", pg_html, re.IGNORECASE):
            findings.append(
                ScanFinding(
                    category="conversion",
                    severity="low",
                    title="Form lacks privacy or trust language",
                    description="A contact/lead form was found without visible privacy assurance or trust language near the submit button.",
                    remediation="Add a short note like 'We never share your info' or link to a privacy policy near the form submit.",
                    evidence=WebsiteEvidence(page_url=url, screenshot_path=shot_map.get(url)),
                    confidence=0.72,
                )
            )

        # Security: form submitting to HTTP endpoint (data exposure risk)
        _form_https = _check_form_https_action(pg_html, url)
        if _form_https is not None:
            findings.append(_form_https)

        # Security: open-redirect pattern in page links (v20)
        _open_redirect = _check_open_redirect_params(pg_html, url)
        if _open_redirect is not None:
            findings.append(_open_redirect)

        # SEO: deprecated/obsolete HTML elements (v21)
        _deprecated_html = _check_deprecated_html_elements(pg_html, url)
        if _deprecated_html is not None:
            findings.append(_deprecated_html)

        # ADA: positive tabindex values disrupting keyboard navigation (v21)
        _pos_tabindex = _check_positive_tabindex(pg_html, url)
        if _pos_tabindex is not None:
            findings.append(_pos_tabindex)

        # Performance: excessive inline styles hindering CSS caching (v21)
        _inline_styles = _check_excessive_inline_styles(pg_html, url)
        if _inline_styles is not None:
            findings.append(_inline_styles)

        # SEO/ADA: generic non-descriptive anchor text (v22)
        _generic_anchor = _check_anchor_text_generic(pg_html, url)
        if _generic_anchor is not None:
            findings.append(_generic_anchor)

        # Security: target=_blank links without rel=noopener noreferrer (v22)
        _blank_target = _check_external_link_security(pg_html, url)
        if _blank_target is not None:
            findings.append(_blank_target)

        # SEO: malformed JSON-LD structured data blocks (v22)
        _schema_errors = _check_structured_data_errors(pg_html, url)
        if _schema_errors is not None:
            findings.append(_schema_errors)

        # SEO: og:title present but og:description missing (v23)
        _og_desc_finding = _check_missing_og_description(pg_html, url)
        if _og_desc_finding is not None:
            findings.append(_og_desc_finding)

        # SEO: legacy meta keywords tag still present (v23)
        _meta_kw_finding = _check_meta_keywords_legacy(pg_html, url)
        if _meta_kw_finding is not None:
            findings.append(_meta_kw_finding)

        # ADA: data tables without <th> header cells (v23)
        _table_a11y_finding = _check_table_accessibility(pg_html, url)
        if _table_a11y_finding is not None:
            findings.append(_table_a11y_finding)

        # ADA: autoplaying video/audio without muted attribute (v23)
        _autoplay_finding = _check_autoplaying_media(pg_html, url)
        if _autoplay_finding is not None:
            findings.append(_autoplay_finding)

        # ADA: CSS suppresses focus outline — keyboard navigation broken (v24)
        _focus_outline_finding = _check_focus_outline_suppressed(pg_html, url)
        if _focus_outline_finding is not None:
            findings.append(_focus_outline_finding)

        # Conversion: form without explicit submit button (v24)
        _submit_btn_finding = _check_form_submit_button(pg_html, url)
        if _submit_btn_finding is not None:
            findings.append(_submit_btn_finding)

        # ADA: HTML lang attribute missing regional subtag (v24, root URL only)
        if url == root_url:
            _lang_region_finding = _check_html_lang_region(pg_html, url)
            if _lang_region_finding is not None:
                findings.append(_lang_region_finding)

        # ADA: auto-rotating carousel without pause controls (v24)
        _carousel_finding = _check_carousel_autorotation(pg_html, url)
        if _carousel_finding is not None:
            findings.append(_carousel_finding)

        # SEO: canonical tag pointing away from current inner page (v24)
        _canonical_mismatch_finding = _check_canonical_mismatch(pg_html, url, root_url)
        if _canonical_mismatch_finding is not None:
            findings.append(_canonical_mismatch_finding)

        # ADA: video elements missing captions track (v25)
        _video_captions_finding = _check_video_captions_absent(pg_html, url)
        if _video_captions_finding is not None:
            findings.append(_video_captions_finding)

        # ADA: autocomplete=off disables personal field autofill (v25)
        _autocomplete_off_finding = _check_autocomplete_off_personal_fields(pg_html, url)
        if _autocomplete_off_finding is not None:
            findings.append(_autocomplete_off_finding)

        # ADA: placeholder text used as only label on form inputs (v25)
        _placeholder_label_finding = _check_placeholder_as_label(pg_html, url)
        if _placeholder_label_finding is not None:
            findings.append(_placeholder_label_finding)

        # ADA: PDF links without file-type warning in anchor text (v25)
        _pdf_link_finding = _check_pdf_links_without_warning(pg_html, url)
        if _pdf_link_finding is not None:
            findings.append(_pdf_link_finding)

        # SEO: breadcrumb nav present but no BreadcrumbList schema (v25, inner pages only)
        _breadcrumb_schema_finding = _check_missing_breadcrumb_schema(pg_html, url, root_url)
        if _breadcrumb_schema_finding is not None:
            findings.append(_breadcrumb_schema_finding)

        # Performance: JPEG/PNG images without next-gen WebP/AVIF format (v26)
        _next_gen_img_finding = _check_next_gen_image_formats(pg_html, url)
        if _next_gen_img_finding is not None:
            findings.append(_next_gen_img_finding)

        # SEO: FAQ content without FAQPage schema markup (v26)
        _faq_schema_finding = _check_missing_faq_schema(pg_html, url)
        if _faq_schema_finding is not None:
            findings.append(_faq_schema_finding)

        # SEO: physical address text without semantic HTML/schema (v26, root URL only)
        if url == root_url:
            _address_element_finding = _check_missing_address_element(pg_html, url)
            if _address_element_finding is not None:
                findings.append(_address_element_finding)

        # Security: form collecting personal data without privacy policy link (v26)
        if has_form:
            _form_privacy_finding = _check_consent_form_privacy_link(pg_html, url)
            if _form_privacy_finding is not None:
                findings.append(_form_privacy_finding)

        # ADA: viewport meta blocks pinch-to-zoom (v27 — WCAG 1.4.4)
        _viewport_scalable_finding = _check_viewport_user_scalable(pg_html, url)
        if _viewport_scalable_finding is not None:
            findings.append(_viewport_scalable_finding)

        # Performance: duplicate GA4/UA analytics tracking IDs on same page (v27)
        _analytics_dup_finding = _check_analytics_duplicate_fire(pg_html, url)
        if _analytics_dup_finding is not None:
            findings.append(_analytics_dup_finding)

        # SEO: missing meta description tag (v27)
        _meta_desc_finding = _check_missing_meta_description(pg_html, url)
        if _meta_desc_finding is not None:
            findings.append(_meta_desc_finding)

        # SEO: images with filename-style alt text (v27)
        _alt_filename_finding = _check_image_alt_filename(pg_html, url)
        if _alt_filename_finding is not None:
            findings.append(_alt_filename_finding)

        # Security: form with sensitive inputs using GET method (v27)
        if has_form:
            _form_get_finding = _check_form_method_get_sensitive(pg_html, url)
            if _form_get_finding is not None:
                findings.append(_form_get_finding)

        # ADA: CSS animations without prefers-reduced-motion override (v28 — WCAG 2.3.3)
        _reduced_motion_finding = _check_css_animation_reduced_motion(pg_html, url)
        if _reduced_motion_finding is not None:
            findings.append(_reduced_motion_finding)

        # Performance: external domains without dns-prefetch/preconnect hints (v28)
        _resource_hint_finding = _check_external_resource_no_hint(pg_html, url)
        if _resource_hint_finding is not None:
            findings.append(_resource_hint_finding)

        # Conversion: no social sharing buttons on content-rich inner pages (v28)
        _social_share_finding = _check_social_sharing_absent(pg_html, url, root_url)
        if _social_share_finding is not None:
            findings.append(_social_share_finding)

        # Security: excessive inline JavaScript event handlers (v29 — OWASP A03:2021)
        _inline_handlers_finding = _check_inline_event_handlers(pg_html, url)
        if _inline_handlers_finding is not None:
            findings.append(_inline_handlers_finding)

        # SEO: missing WebSite JSON-LD schema on homepage (v29)
        _website_schema_finding = _check_missing_website_schema(pg_html, url, root_url)
        if _website_schema_finding is not None:
            findings.append(_website_schema_finding)

        # ADA: <select> dropdowns without accessible label associations (v30 — WCAG 1.3.1)
        if has_form or SELECT_ELEMENT_RE.search(pg_html):
            _select_label_finding = _check_select_without_label(pg_html, url)
            if _select_label_finding is not None:
                findings.append(_select_label_finding)

        # Conversion: no CTA visible in above-fold homepage content (v30)
        _above_fold_finding = _check_above_fold_cta(pg_html, url, root_url)
        if _above_fold_finding is not None:
            findings.append(_above_fold_finding)

        # Performance: unminified non-CDN JS/CSS resources (v30)
        _unmin_finding = _check_unminified_resources(pg_html, url)
        if _unmin_finding is not None:
            findings.append(_unmin_finding)

        # SEO: content-rich page with no H2 subheadings (v30 — WCAG 2.4.6)
        _h2_finding = _check_missing_h2_headings(pg_html, url)
        if _h2_finding is not None:
            findings.append(_h2_finding)

        # SEO: homepage with blog/news nav but no RSS feed discovery link (v31)
        _rss_finding = _check_rss_feed_absent(pg_html, url, root_url)
        if _rss_finding is not None:
            findings.append(_rss_finding)

        # SEO: og:title present but twitter:card absent — plain-text social previews (v31)
        _twitter_card_finding = _check_missing_twitter_card(pg_html, url)
        if _twitter_card_finding is not None:
            findings.append(_twitter_card_finding)

        # Performance: synchronous non-CDN scripts in body delay Time to Interactive (v31)
        _body_scripts_finding = _check_body_render_blocking_scripts(pg_html, url)
        if _body_scripts_finding is not None:
            findings.append(_body_scripts_finding)

        # Conversion: form inputs using type='text' for email/phone fields (v32)
        if has_form:
            _input_type_finding = _check_input_type_validation(pg_html, url)
            if _input_type_finding is not None:
                findings.append(_input_type_finding)

        # SEO: inner page with no H1 tag at all (v32)
        _missing_h1_finding = _check_missing_page_h1(pg_html, url, root_url)
        if _missing_h1_finding is not None:
            findings.append(_missing_h1_finding)

        # ADA: multiple nav elements without distinct aria-label attributes (v32)
        _nav_label_finding = _check_nav_aria_label(pg_html, url)
        if _nav_label_finding is not None:
            findings.append(_nav_label_finding)

        # SEO: meta robots nofollow blocking link equity on page (v32)
        _nofollow_finding = _check_meta_robots_nofollow(pg_html, url)
        if _nofollow_finding is not None:
            findings.append(_nofollow_finding)

        # SEO: og:title present but og:image missing — incomplete Open Graph (v33)
        _og_image_finding = _check_missing_og_image(pg_html, url)
        if _og_image_finding is not None:
            findings.append(_og_image_finding)

        # ADA: link underlines suppressed without hover restore — WCAG 1.4.1 (v33)
        _link_nodecor_finding = _check_link_underline_suppressed(pg_html, url)
        if _link_nodecor_finding is not None:
            findings.append(_link_nodecor_finding)

        # ADA: linked images with empty alt text — WCAG 4.1.2 empty link (v33)
        _empty_alt_link_finding = _check_empty_alt_link_images(pg_html, url)
        if _empty_alt_link_finding is not None:
            findings.append(_empty_alt_link_finding)

        # Performance: Google Fonts without display=swap — FOIT risk (v34)
        _font_swap_finding = _check_font_display_swap(pg_html, url)
        if _font_swap_finding is not None:
            findings.append(_font_swap_finding)

        # ADA: buttons without accessible name — WCAG 4.1.2 (v34)
        _button_name_finding = _check_button_accessible_name(pg_html, url)
        if _button_name_finding is not None:
            findings.append(_button_name_finding)

        # SEO: pricing content without Offer/Product schema — missed rich results (v34)
        _price_schema_finding = _check_price_schema_missing(pg_html, url)
        if _price_schema_finding is not None:
            findings.append(_price_schema_finding)

        # Performance: no resource preload hints for LCP-critical assets (v34)
        _preload_finding = _check_preload_key_requests(pg_html, url)
        if _preload_finding is not None:
            findings.append(_preload_finding)

        # SEO: page title too long or too short — SERP truncation risk (v35)
        _title_length_finding = _check_page_title_length(pg_html, url)
        if _title_length_finding is not None:
            findings.append(_title_length_finding)

        # Performance: apple-touch-icon missing — iOS homescreen degraded (v35, root_url only)
        _touch_icon_finding = _check_apple_touch_icon_missing(pg_html, url, root_url)
        if _touch_icon_finding is not None:
            findings.append(_touch_icon_finding)

        # Performance: 3+ Google Font families — font loading waterfall (v35)
        _multi_font_finding = _check_multiple_font_families(pg_html, url)
        if _multi_font_finding is not None:
            findings.append(_multi_font_finding)

        # Security: contact form without spam protection (v35, inside has_form block)
        if has_form:
            _spam_protection_finding = _check_form_spam_protection_absent(pg_html, url)
            if _spam_protection_finding is not None:
                findings.append(_spam_protection_finding)

        # Performance: 4+ marketing tracking pixels — compounding render latency (v36)
        _tracking_overload_finding = _check_tracking_pixel_overload(pg_html, url)
        if _tracking_overload_finding is not None:
            findings.append(_tracking_overload_finding)

        # Security: raw email address exposed in HTML — spam harvesting risk (v36)
        _email_exposure_finding = _check_html_email_exposure(pg_html, url)
        if _email_exposure_finding is not None:
            findings.append(_email_exposure_finding)

        # SEO: homepage without Organization JSON-LD schema — Knowledge Panel gap (v36)
        _org_schema_finding = _check_missing_organization_schema(pg_html, url, root_url)
        if _org_schema_finding is not None:
            findings.append(_org_schema_finding)

        # Performance: image-heavy page with <30% lazy-loaded images — LCP delay (v36)
        _lazy_coverage_finding = _check_image_lazy_loading_coverage(pg_html, url)
        if _lazy_coverage_finding is not None:
            findings.append(_lazy_coverage_finding)

        # SEO: paginated archive page missing rel=prev/next link elements (v37)
        _pagination_finding = _check_pagination_rel_links(pg_html, url, root_url)
        if _pagination_finding is not None:
            findings.append(_pagination_finding)

        # SEO: blog/news inner page missing Article/BlogPosting JSON-LD schema (v37)
        _article_schema_finding = _check_missing_article_schema(pg_html, url, root_url)
        if _article_schema_finding is not None:
            findings.append(_article_schema_finding)

        # Conversion: homepage footer without phone/email/address contact info (v37)
        _footer_contact_finding = _check_footer_contact_missing(pg_html, url, root_url)
        if _footer_contact_finding is not None:
            findings.append(_footer_contact_finding)

        # SEO: anchor links pointing to on-page IDs that don't exist (v37)
        _broken_anchors_finding = _check_broken_anchor_links(pg_html, url)
        if _broken_anchors_finding is not None:
            findings.append(_broken_anchors_finding)

        # Performance: same external script src loaded more than once (v37)
        _dup_scripts_finding = _check_duplicate_script_tags(pg_html, url)
        if _dup_scripts_finding is not None:
            findings.append(_dup_scripts_finding)

        # ADA: images with ≤2-char meaningless alt text (v38)
        _short_alt_finding = _check_image_alt_short_text(pg_html, url)
        if _short_alt_finding is not None:
            findings.append(_short_alt_finding)

        # SEO: keyword-stuffed H1/H2 heading detected (v38)
        _kw_stuff_finding = _check_heading_keyword_stuffing(pg_html, url)
        if _kw_stuff_finding is not None:
            findings.append(_kw_stuff_finding)

        # Performance: GA4 analytics without preconnect hint (v38, homepage only)
        _analytics_preconnect_finding = _check_analytics_preconnect_missing(pg_html, url, root_url)
        if _analytics_preconnect_finding is not None:
            findings.append(_analytics_preconnect_finding)

        # ADA: form required fields without ARIA live region for error messages (v38)
        _form_error_finding = _check_form_error_handling_absent(pg_html, url)
        if _form_error_finding is not None:
            findings.append(_form_error_finding)

        # SEO: missing charset declaration in page head (v38)
        _charset_finding = _check_charset_declaration_missing(pg_html, url)
        if _charset_finding is not None:
            findings.append(_charset_finding)

        # ADA: no skip navigation link — WCAG 2.4.1 Bypass Blocks (v39)
        _skip_nav_finding = _check_skip_nav_link(pg_html, url)
        if _skip_nav_finding is not None:
            findings.append(_skip_nav_finding)

        # Security: external CSS stylesheets loaded without SRI integrity attr (v39)
        _css_sri_finding = _check_external_css_sri(pg_html, url)
        if _css_sri_finding is not None:
            findings.append(_css_sri_finding)

        # ADA: <html> element has no lang attribute at all — WCAG 3.1.1 (v39, homepage only)
        _lang_missing_finding = _check_html_lang_attribute_missing(pg_html, url, root_url)
        if _lang_missing_finding is not None:
            findings.append(_lang_missing_finding)

        # ADA: form with ≥6 inputs without <fieldset> grouping — WCAG 1.3.1 (v39)
        if has_form:
            _fieldset_finding = _check_form_fieldset_grouping(pg_html, url)
            if _fieldset_finding is not None:
                findings.append(_fieldset_finding)

        # Performance: missing web app manifest link — PWA/homescreen capability (v40)
        _manifest_finding = _check_manifest_json_missing(pg_html, url, root_url)
        if _manifest_finding is not None:
            findings.append(_manifest_finding)

        # Performance: excessive DOM size — Google Lighthouse threshold (v40)
        _dom_size_finding = _check_excessive_dom_size(pg_html, url)
        if _dom_size_finding is not None:
            findings.append(_dom_size_finding)

        # SEO: inner page missing self-referential canonical tag (v40)
        _self_canonical_finding = _check_self_referential_canonical_missing(pg_html, url, root_url)
        if _self_canonical_finding is not None:
            findings.append(_self_canonical_finding)

        # Conversion: phone/zip inputs without type=tel/pattern — missed mobile UX (v40)
        if has_form:
            _input_pattern_finding = _check_input_pattern_missing(pg_html, url)
            if _input_pattern_finding is not None:
                findings.append(_input_pattern_finding)

        # ADA: no viewport meta tag on homepage — mobile rendering completely broken (v41)
        _viewport_missing_finding = _check_meta_viewport_missing(pg_html, url, root_url)
        if _viewport_missing_finding is not None:
            findings.append(_viewport_missing_finding)

        # ADA: SVG icons without aria-hidden or role=img labeling (v41)
        _svg_aria_finding = _check_svg_icon_aria_missing(pg_html, url)
        if _svg_aria_finding is not None:
            findings.append(_svg_aria_finding)

        # Conversion: long-form page without back-to-top navigation (v41)
        _back_to_top_finding = _check_long_content_no_back_to_top(pg_html, url)
        if _back_to_top_finding is not None:
            findings.append(_back_to_top_finding)

        # SEO: multiple competing canonical tags on a single page (v41)
        _multi_canonical_finding = _check_multiple_canonical_tags(pg_html, url)
        if _multi_canonical_finding is not None:
            findings.append(_multi_canonical_finding)

        # Security: external iframes without sandbox attribute (v41)
        _iframe_sandbox_finding = _check_iframe_sandbox_missing(pg_html, url)
        if _iframe_sandbox_finding is not None:
            findings.append(_iframe_sandbox_finding)

        # Security: password field without autocomplete protection
        if PASSWORD_INPUT_RE.search(pg_html) and not AUTOCOMPLETE_OFF_RE.search(pg_html):
            findings.append(
                ScanFinding(
                    category="security",
                    severity="medium",
                    title="Password field missing autocomplete protection",
                    description=(
                        "A password input field was found without autocomplete=\"off\" or "
                        "autocomplete=\"new-password\" attribute. Browsers and password managers may "
                        "auto-fill credentials into unintended forms, and malicious browser extensions "
                        "can silently harvest stored credentials via unprotected inputs."
                    ),
                    remediation=(
                        'Add autocomplete="current-password" to login fields and autocomplete="new-password" '
                        "to registration/reset forms. For sensitive admin inputs, use autocomplete=\"off\" "
                        "to prevent browser storage entirely."
                    ),
                    evidence=WebsiteEvidence(page_url=url, screenshot_path=shot_map.get(url)),
                    confidence=0.82,
                )
            )

        # SEO: meta refresh redirect detected
        if META_REFRESH_RE.search(pg_html):
            findings.append(
                ScanFinding(
                    category="seo",
                    severity="medium",
                    title="Meta refresh redirect detected",
                    description=(
                        "A <meta http-equiv='refresh'> tag was found on this page. Meta refresh redirects "
                        "are considered a poor practice: they delay user experience, are flagged as suspicious "
                        "by Google, and pass reduced link equity compared to proper 301 redirects. "
                        "Some browsers warn users before following meta refresh redirects."
                    ),
                    remediation=(
                        "Replace the meta refresh with a server-side 301 (permanent) redirect configured "
                        "in your .htaccess, nginx config, or hosting control panel. "
                        "This preserves full link equity and loads the destination immediately."
                    ),
                    evidence=WebsiteEvidence(page_url=url, snippet=META_REFRESH_RE.search(pg_html).group(0)[:120]),
                    confidence=0.90,
                )
            )

        # ADA: form inputs without associated labels + Conversion: form field friction
        if has_form:
            input_count = len(INPUT_TYPE_RE.findall(pg_html))
            label_count = len(LABEL_RE.findall(pg_html))
            if input_count > 0 and label_count < input_count:
                findings.append(
                    ScanFinding(
                        category="ada",
                        severity="medium",
                        title="Form input fields may lack accessible labels",
                        description=(
                            f"Found {input_count} input field(s) but only {label_count} label element(s). "
                            "Screen reader users and keyboard-only users depend on properly associated labels "
                            "to understand form fields. Unlabeled inputs also fail WCAG 2.1 Success Criterion 1.3.1."
                        ),
                        remediation=(
                            "Wrap each input with a <label> element or use aria-label/aria-labelledby attributes. "
                            "Ensure placeholder text is not the only label — placeholders disappear when typing begins."
                        ),
                        evidence=WebsiteEvidence(
                            page_url=url,
                            screenshot_path=shot_map.get(url),
                            metadata={"input_count": input_count, "label_count": label_count},
                        ),
                        confidence=0.76,
                    )
                )
            # ADA: email/tel inputs without autocomplete attribute (v22 — WCAG 1.3.5)
            _autocomplete_missing = _check_input_autocomplete_missing(pg_html, url)
            if _autocomplete_missing is not None:
                findings.append(_autocomplete_missing)

            # Conversion: excessive form fields create friction
            _friction_count = _check_form_field_friction(pg_html)
            if _friction_count is not None:
                findings.append(
                    ScanFinding(
                        category="conversion",
                        severity="medium" if _friction_count >= 8 else "low",
                        title=f"Lead form has excessive fields ({_friction_count} inputs)",
                        description=(
                            f"Found {_friction_count} input fields on this form. Research shows forms with "
                            "3–4 fields convert at 2–3× the rate of forms with 6+ fields — each additional "
                            "field reduces completion rate by approximately 10–15%."
                        ),
                        remediation=(
                            "Reduce the form to the essential minimum (name, email, message/service type). "
                            "Move optional fields to a follow-up step or mark them clearly optional. "
                            "A/B test a simplified 3-field version against the current form to measure lift."
                        ),
                        evidence=WebsiteEvidence(
                            page_url=url,
                            screenshot_path=shot_map.get(url),
                            metadata={"input_count": _friction_count},
                        ),
                        confidence=0.80,
                    )
                )

        # Conversion: no testimonials/social proof
        if not has_testimonials and url == root_url:
            findings.append(
                ScanFinding(
                    category="conversion",
                    severity="medium",
                    title="No visible social proof or testimonials",
                    description="No customer reviews, ratings, or testimonials were detected on the homepage. Social proof is critical for SMB conversion.",
                    remediation="Add 2–3 genuine client testimonials with photos and names (or link to Google Reviews). Showcase star ratings if applicable.",
                    evidence=WebsiteEvidence(page_url=url, screenshot_path=shot_map.get(url)),
                    confidence=0.74,
                )
            )

        # Conversion: no phone number
        if not has_phone and url == root_url:
            findings.append(
                ScanFinding(
                    category="conversion",
                    severity="low",
                    title="Phone number not prominently visible",
                    description="A click-to-call phone number on the homepage is a top conversion driver for service businesses.",
                    remediation="Add a phone number in the header or hero section and ensure it's tappable on mobile.",
                    evidence=WebsiteEvidence(page_url=url),
                    confidence=0.68,
                )
            )

        # Conversion: phone number found but not click-to-call (no tel: link)
        if has_phone and url == root_url and not TEL_LINK_RE.search(pg_html):
            findings.append(
                ScanFinding(
                    category="conversion",
                    severity="medium",
                    title="Phone number not wrapped in click-to-call link",
                    description=(
                        "A phone number was detected on the homepage but is not wrapped in a tel: hyperlink. "
                        "Mobile users — who often represent the majority of service business traffic — cannot tap the number to call directly. "
                        "Un-linked phone numbers create friction precisely when a prospect is ready to act."
                    ),
                    remediation=(
                        'Wrap all phone numbers in a tel: anchor: <a href="tel:+1XXXXXXXXXX">(XXX) XXX-XXXX</a>. '
                        "Apply this everywhere the number appears: header, hero section, and footer. "
                        "Most page builders (Elementor, Squarespace, Wix) have a phone/button widget with click-to-call built in."
                    ),
                    evidence=WebsiteEvidence(page_url=url, screenshot_path=shot_map.get(url)),
                    confidence=0.82,
                )
            )

        # SEO: canonical link on root
        if url == root_url:
            canonical_match = CANONICAL_RE.search(pg_html[:3000])
            if not canonical_match:
                findings.append(
                    ScanFinding(
                        category="seo",
                        severity="low",
                        title="No canonical URL tag on homepage",
                        description="A canonical tag prevents duplicate content issues when pages are accessible via multiple URLs (with/without www, trailing slash, etc.).",
                        remediation='Add <link rel="canonical" href="https://yourdomain.com/"> to the homepage <head> section.',
                        evidence=WebsiteEvidence(page_url=url),
                        confidence=0.80,
                    )
                )

        # SEO: Schema present but no LocalBusiness type declared
        if has_schema and url == root_url:
            _schema_completeness = _check_schema_completeness(pg_html, url)
            if _schema_completeness is not None:
                findings.append(_schema_completeness)
            elif not LOCAL_BUSINESS_SCHEMA_RE.search(pg_html):
                findings.append(
                    ScanFinding(
                        category="seo",
                        severity="low",
                        title="Schema markup lacks LocalBusiness type",
                        description=(
                            "JSON-LD structured data was detected but does not appear to include a LocalBusiness "
                            "(or applicable subtype like ProfessionalService, Restaurant, etc.) schema type. "
                            "LocalBusiness schema unlocks local knowledge panel entries and rich SERP features "
                            "including hours, phone number, and address — critical for local search visibility."
                        ),
                        remediation=(
                            "Add a LocalBusiness JSON-LD block with: @type, name, address, telephone, url, "
                            "and openingHoursSpecification. Use the most specific applicable subtype. "
                            "Validate using Google's Rich Results Test at https://search.google.com/test/rich-results."
                        ),
                        evidence=WebsiteEvidence(page_url=url),
                        confidence=0.76,
                    )
                )

        # SEO: LocalBusiness present but no Review/AggregateRating schema (v20)
        if url == root_url:
            _review_schema = _check_schema_review_rating(pg_html, url)
            if _review_schema is not None:
                findings.append(_review_schema)

        # SEO: Thin content on inner pages (skip homepage — typically navigation-heavy)
        if url != root_url:
            stripped_words = WORD_CONTENT_RE.findall(re.sub(r"<[^>]+>", " ", pg_html))
            word_count = len(stripped_words)
            if word_count < 200:
                findings.append(
                    ScanFinding(
                        category="seo",
                        severity="low",
                        title=f"Thin page content detected ({word_count} words)",
                        description=(
                            f"This page contains approximately {word_count} words of readable content. "
                            "Google may classify pages with under 200 words as 'thin content', which can reduce "
                            "page ranking potential and trigger a site-wide quality signal issue."
                        ),
                        remediation=(
                            "Expand the page with useful, relevant content: service descriptions, location-specific copy, "
                            "FAQs, client outcomes, or how-to guidance. Aim for 300–600 words on service pages. "
                            "Avoid padding with keyword stuffing — focus on genuine value for the visitor."
                        ),
                        evidence=WebsiteEvidence(page_url=url, metadata={"word_count": word_count}),
                        confidence=0.74,
                    )
                )

    # --- SEO: Cross-page duplicate title tags ---
    for norm_title, urls_with_title in _detect_duplicate_page_titles(pages):
        findings.append(
            ScanFinding(
                category="seo",
                severity="medium",
                title="Duplicate page titles across multiple pages",
                description=(
                    f"The same title tag ('{norm_title[:70]}') appears on {len(urls_with_title)} pages. "
                    "Duplicate titles confuse search engines about which page to rank for shared keywords, "
                    "dilute the unique keyword signal each page should establish, and reduce overall site authority."
                ),
                remediation=(
                    "Write a unique title for every page that reflects its specific content, primary keyword, "
                    "and location (for local businesses). Format: 'Primary Keyword | Business Name — City, State'. "
                    "Never copy-paste titles across pages — even for similar services, differentiate by location or specialty."
                ),
                evidence=WebsiteEvidence(
                    page_url=urls_with_title[0],
                    snippet=norm_title[:100],
                    metadata={"affected_pages": len(urls_with_title), "example_title": norm_title[:100]},
                ),
                confidence=0.90,
            )
        )

    # --- Performance: page load time and payload ---
    slow_pages = [(u, t) for u, t in load_times.items() if t > 4.0]
    if slow_pages:
        slowest_url, slowest_time = max(slow_pages, key=lambda x: x[1])
        findings.append(
            ScanFinding(
                category="performance",
                severity="medium",
                title=f"Slow page load time detected ({slowest_time:.1f}s)",
                description=f"One or more pages took over 4 seconds to load. Google penalizes slow sites in rankings, and 53% of mobile users leave after 3s.",
                remediation="Enable caching, compress images (WebP format), minify CSS/JS, and consider a CDN like Cloudflare (free tier).",
                evidence=WebsiteEvidence(page_url=slowest_url, metadata={"load_times": {u: round(t, 2) for u, t in load_times.items()}}),
                confidence=0.80,
            )
        )

    heavy_pages = [u for u, h in pages.items() if len(h) > 600_000]
    if heavy_pages:
        findings.append(
            ScanFinding(
                category="performance",
                severity="medium",
                title="Large page HTML payload detected",
                description=f"One or more pages have unusually large HTML (>{len(pages[heavy_pages[0]])//1000}KB). This suggests unoptimized inline content.",
                remediation="Minimize inline scripts, defer third-party widgets, and move large content to lazy-loaded sections.",
                evidence=WebsiteEvidence(page_url=heavy_pages[0], metadata={"size_bytes": len(pages[heavy_pages[0]])}),
                confidence=0.76,
            )
        )

    # --- SEO: broken internal links (probes hrefs not yet crawled) ---
    _broken_links_finding = _check_broken_internal_links(pages, root_url)
    if _broken_links_finding is not None:
        findings.append(_broken_links_finding)

    # --- SEO: inner pages blocked from indexing via noindex (v18) ---
    _noindex_inner_finding = _check_noindex_inner_pages(pages, root_url)
    if _noindex_inner_finding is not None:
        findings.append(_noindex_inner_finding)

    # --- SEO: duplicate meta descriptions across crawled pages (v20) ---
    _dup_meta_finding = _check_duplicate_meta_descriptions(pages)
    if _dup_meta_finding is not None:
        findings.append(_dup_meta_finding)

    # --- SEO: inconsistent title separator style across crawled pages (v26) ---
    _title_sep_finding = _check_title_separator_inconsistency(pages)
    if _title_sep_finding is not None:
        findings.append(_title_sep_finding)

    # --- SEO: same H1 text on 2+ different pages (v28) ---
    _dup_h1_finding = _check_duplicate_h1_across_pages(pages)
    if _dup_h1_finding is not None:
        findings.append(_dup_h1_finding)

    # --- SEO: robots.txt blocking CSS/JS assets from Googlebot rendering (v28) ---
    _robots_assets_finding = _check_robots_blocks_assets(robots.get("raw", ""), root_url)
    if _robots_assets_finding is not None:
        findings.append(_robots_assets_finding)

    # --- SEO: robots.txt missing Sitemap: directive — slower search engine discovery (v36) ---
    _robots_sitemap_finding = _check_robots_sitemap_directive(robots.get("raw", ""), root_url)
    if _robots_sitemap_finding is not None:
        findings.append(_robots_sitemap_finding)

    # --- SEO: soft 404 pages — inner pages returning 200 with 'not found' body text (v29) ---
    _soft_404_finding = _check_soft_404_pages(pages, root_url)
    if _soft_404_finding is not None:
        findings.append(_soft_404_finding)

    # --- Security: no DNS CAA record — unrestricted TLS certificate issuance (v31) ---
    _host_for_caa = urlparse(root_url).hostname or ""
    if _host_for_caa:
        _caa_finding = _check_dns_caa_record(_host_for_caa)
        if _caa_finding is not None:
            findings.append(_caa_finding)

    # --- SEO: same H2 heading text on 3+ pages — thin/templated content signal (v32) ---
    _dup_h2_finding = _check_duplicate_h2_headings(pages)
    if _dup_h2_finding is not None:
        findings.append(_dup_h2_finding)

    # --- SEO: <30% of crawled pages have any JSON-LD structured data (v39) ---
    _schema_coverage_finding = _check_structured_data_coverage(pages, root_url)
    if _schema_coverage_finding is not None:
        findings.append(_schema_coverage_finding)

    # --- SEO: partial hreflang implementation — some pages have annotations, others don't (v40) ---
    _hreflang_finding = _check_hreflang_inconsistency(pages)
    if _hreflang_finding is not None:
        findings.append(_hreflang_finding)

    # --- SEO: external crawler layer via site-audit-seo (npm) ---
    findings.extend(_run_site_audit_seo_external(base_url=base_url, out_dir=out_dir))

    _sanitize_findings(findings)
    for finding in findings:
        validate_finding(finding)

    # Collapse same (category, title) duplicates that arise from multi-page crawls.
    # Keep the highest-confidence representative; enrich description with pages_affected count.
    title_groups: dict[tuple[str, str], list[ScanFinding]] = {}
    for f in findings:
        title_groups.setdefault((f.category, f.title), []).append(f)
    deduped: list[ScanFinding] = []
    for (cat, title), group in title_groups.items():
        if len(group) == 1:
            deduped.append(group[0])
        else:
            best = max(group, key=lambda x: x.confidence)
            affected = [f.evidence.page_url for f in group if f.evidence.page_url]
            pages_note = (
                f" ({len(group)} pages affected: {', '.join(str(u) for u in affected[:2])}"
                + (f" +{len(affected) - 2} more" if len(affected) > 2 else "")
                + ".)"
            )
            deduped.append(
                ScanFinding(
                    category=best.category,
                    severity=best.severity,
                    title=best.title,
                    description=best.description + pages_note,
                    remediation=best.remediation,
                    evidence=best.evidence,
                    confidence=best.confidence,
                )
            )
    findings = deduped

    return {
        "base_url": root_url,
        "pages": page_urls,
        "screenshots": shot_map,
        "tls": tls,
        "dns_auth": dns_auth,
        "robots": robots,
        "exposed_files": exposed_hits,
        "load_times": {u: round(t, 2) for u, t in load_times.items()},
        "findings": findings,
        "finding_dicts": [_finding_to_dict(f) for f in findings],
    }
