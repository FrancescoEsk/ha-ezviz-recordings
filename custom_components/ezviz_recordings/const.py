"""Constants for EZVIZ Recordings."""

DOMAIN = "ezviz_recordings"
SOURCE_DOMAIN = "ezviz_rtsp"

# configuration.yaml options (all optional)
CONF_STORAGE_DIR = "storage_dir"
CONF_STORAGE_LIMIT_GB = "storage_limit_gb"
CONF_REQUIRE_MOUNT = "require_mount"
CONF_AUTO_DOWNLOAD = "auto_download"
CONF_AUTO_HOURS = "auto_hours"
CONF_MIN_BATTERY = "min_battery"
CONF_MEDIA_DAYS = "media_days"
CONF_LANGUAGE = "language"  # Media panel labels; default = HA system language

DEFAULT_STORAGE_DIR = "/media/ezviz_recordings"
DEFAULT_STORAGE_LIMIT_GB = 20  # above this the oldest days (clips + thumbnails) are deleted
DEFAULT_AUTO_HOURS = [3, 6, 9, 12, 15, 18, 21]  # automatic download of today's clips (+ 23:50)
DEFAULT_MIN_BATTERY = 20  # skip a camera below this battery level
DEFAULT_MEDIA_DAYS = 30  # days listed per camera in the Media panel

# HTTP endpoints (auth required, handed out as signed URLs)
CLIP_URL = "/api/ezviz_recordings/clip"
THUMB_URL = "/api/ezviz_recordings/thumb"
SIGNED_URL_HOURS = 6
CARD_FILE = "ezviz-recordings-card.js"
CARD_URL = "/ezviz_recordings_static/ezviz-recordings-card.js"

THUMB_WIDTH = 192  # px; shown at 64x36 in the card (sharp on hi-dpi screens)
MIN_COMPLETE_RATIO = 0.6  # a saved clip shorter than this share of its record is truncated
STILL_RECORDING_MIN = 2  # a clip that ended less than this ago may still be growing

LIST_CACHE_SECONDS = 60  # today's list counts as fresh for this long
COMPLETE_MARGIN_MIN = 30  # a day fetched this long after midnight is final
RECORDS_KEEP_DAYS = 120  # forget cached lists older than this (unless files of that day are saved)

AUTO_SECOND_PASS_DELAY = 60  # s before retrying cameras that failed in the same run
AUTO_RETRY_DELAY = 900  # s; one more run later if some camera still failed
SYNC_MIN_INTERVAL = 600  # s; opening the card downloads new clips at most this often
