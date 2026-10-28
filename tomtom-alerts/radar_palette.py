"""
RainViewer radar tile color classification.

RainViewer's free tier ignores the `color` URL parameter (verified empirically:
requesting color=0,1,2,8 for the same frame/tile returns byte-identical PNGs) and
is pinned to one fixed palette. Captured live on 2026-08-03 by sweeping a global
zoom-2 mosaic plus a UK zoom-7 mosaic (76 distinct RGBA values total, tile options
"0_0" i.e. no smoothing/snow-blend, which is load-bearing — smoothing interpolates
colors that don't exist in this classification and would break it).

The palette turned out to be five clean, structurally-separable families rather
than a scattered lookup table, so classification is done by channel comparison
(validated against all 76 captured values, zero unclassified) instead of a large
hardcoded table — this also degrades gracefully (routes to UNKNOWN, the decode-
failure signal) if RainViewer ever tweaks the palette slightly, rather than
silently mis-binning an untranscribed color.

Bands, weakest -> strongest within each:
  TRANSPARENT     - no radar coverage / no precipitation
  SNOW            - alpha-coded tan ramp (25 shades, alpha 20-190); excluded from
                    rain detection, tracked separately as snow_fraction
  BLUE            - light rain -> moderate rain (20 shades)
  HOT             - yellow -> orange -> dark red: heavy -> very heavy rain (20 shades)
  EXTREME         - magenta family: extreme/hail-signature precipitation (9 shades),
                    always more severe than any HOT shade
  WHITE_ARTIFACT  - (255,255,255,255); rare (23/2.8M pixels in the 2026-08-03
                    capture, none over the UK specifically), behaves like an
                    antialiasing artifact rather than a genuine data value —
                    excluded from positive rain classification
  UNKNOWN         - anything not matching the above; see is_decode_failure()
"""

TRANSPARENT = "TRANSPARENT"
SNOW = "SNOW"
BLUE = "BLUE"
HOT = "HOT"
EXTREME = "EXTREME"
WHITE_ARTIFACT = "WHITE_ARTIFACT"
UNKNOWN = "UNKNOWN"

# Bands counted as "high rainfall" for storm-cell detection. BLUE (light-to-
# moderate rain) is deliberately excluded: checked live against a 2026-08-04
# UK frame, BLUE at any cutoff classified 25.9% of the UK as "high rainfall"
# simultaneously (85% of all high-rain pixels were BLUE) — a threshold that
# permissive isn't detecting flood-risk weather, it's detecting ordinary rain.
# HOT+EXTREME alone gave 3.84% of the UK, a far more defensible "severe" bar.
HIGH_RAIN_BANDS = (HOT, EXTREME)

# Max decode-failure fraction of non-transparent pixels before a cycle is treated
# as a bad frame (log loudly, don't proceed) rather than silently "no rain".
MAX_UNKNOWN_FRACTION = 0.01


def classify_pixel(rgba: tuple[int, int, int, int]) -> tuple[str, int]:
    """
    Classify one RGBA pixel into (band, rank). Rank is 0 for the weakest shade
    in a band and increases with severity; ranks are only comparable within the
    same band except EXTREME, which is always more severe than any HOT rank.
    """
    r, g, b, a = rgba
    if a == 0:
        return TRANSPARENT, 0
    if a != 255:
        return SNOW, a
    if (r, g, b) == (255, 255, 255):
        return WHITE_ARTIFACT, 0
    if b == 255 and r == 255:
        return EXTREME, 255 - g
    if b == 0:
        return HOT, 600 - (r + g + b)
    if b > 0:
        return BLUE, 600 - (r + g + b)
    return UNKNOWN, 0


def is_high_rainfall(band: str, rank: int) -> bool:
    return band in HIGH_RAIN_BANDS


def is_decode_failure(unknown_pixels: int, non_transparent_pixels: int) -> bool:
    if non_transparent_pixels == 0:
        return False
    return (unknown_pixels / non_transparent_pixels) > MAX_UNKNOWN_FRACTION


def classify_array(rgba: "np.ndarray"):
    """
    Vectorized equivalent of classify_pixel()+is_high_rainfall() for a whole
    (H, W, 4) uint8 mosaic.

    Returns:
      high_rain_mask (H,W bool)   - pixels counted as "high rainfall"
      snow_mask      (H,W bool)   - pixels flagged snow (excluded from rain)
      severity       (H,W float)  - informational-only relative intensity for
                                     high_rain_mask pixels (0 elsewhere); EXTREME
                                     always > HOT always > BLUE, not a real unit
      band_code      (H,W int)    - 0=other/transparent/blue (not high-rain), 2=hot, 3=extreme
      unknown_count, non_transparent_count (int)
    """
    import numpy as np

    r = rgba[..., 0].astype(np.int32)
    g = rgba[..., 1].astype(np.int32)
    b = rgba[..., 2].astype(np.int32)
    a = rgba[..., 3].astype(np.int32)

    transparent = a == 0
    opaque = a == 255
    snow = ~transparent & ~opaque

    white = opaque & (r == 255) & (g == 255) & (b == 255)
    extreme = opaque & (b == 255) & (r == 255) & ~white
    hot = opaque & (b == 0) & ~white
    blue = opaque & (b > 0) & (b < 255) & ~white
    unknown = opaque & ~(white | extreme | hot | blue)

    blue_rank = 600 - (r + g + b)
    hot_rank = 600 - (r + g + b)
    extreme_rank = 255 - g

    # BLUE (light-to-moderate rain) never counts as "high" — see HIGH_RAIN_BANDS.
    high_rain = hot | extreme

    severity = np.zeros(rgba.shape[:2], dtype=np.float64)
    severity = np.where(hot, 1000.0 + hot_rank, severity)
    severity = np.where(extreme, 3000.0 + extreme_rank, severity)

    band_code = np.zeros(rgba.shape[:2], dtype=np.int8)
    band_code = np.where(hot, 2, band_code)
    band_code = np.where(extreme, 3, band_code)

    non_transparent_count = int(np.count_nonzero(~transparent))
    unknown_count = int(np.count_nonzero(unknown))

    return high_rain, snow, severity, band_code, unknown_count, non_transparent_count
