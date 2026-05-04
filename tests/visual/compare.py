"""Visual regression utilities — PIL diff with configurable tolerance.

Requires Pillow (`pip install Pillow`).

The compare strategy:
1. Open actual + baseline as RGB
2. Compute pixel-wise difference (PIL ImageChops.difference)
3. Count pixels where any RGB channel diff > pixel_threshold
4. Return ratio of differing pixels / total

Default tolerance is 1% — enough to absorb GTK anti-aliasing and font
hinting variations across runs while still catching real changes
(missing widget, wrong color, layout shift).
"""

from pathlib import Path

from PIL import Image, ImageChops

DEFAULT_TOLERANCE = 0.01           # 1% of pixels
DEFAULT_PIXEL_THRESHOLD = 30        # max RGB component diff to consider "same"


def diff_ratio(actual_path, baseline_path, pixel_threshold=DEFAULT_PIXEL_THRESHOLD):
    """Return ratio (0.0–1.0) of pixels that differ beyond threshold.

    Size mismatch returns 1.0 (total fail) — different layouts mean
    different windows, no point comparing pixel-by-pixel.
    """
    a = Image.open(actual_path).convert("RGB")
    b = Image.open(baseline_path).convert("RGB")
    if a.size != b.size:
        return 1.0
    diff = ImageChops.difference(a, b)
    if diff.getbbox() is None:
        return 0.0
    differing = sum(
        1 for r, g, bl in diff.getdata()
        if max(r, g, bl) > pixel_threshold
    )
    total = a.size[0] * a.size[1]
    return differing / total


def assert_visual_match(
    actual_path,
    baseline_dir,
    name,
    tolerance=DEFAULT_TOLERANCE,
    pixel_threshold=DEFAULT_PIXEL_THRESHOLD,
    update=False,
):
    """Compare `actual_path` against `baseline_dir/{name}.png`.

    First-run / update mode: if baseline missing OR update=True, save
    actual as the new baseline and return (no assertion). To refresh
    all baselines: set BTERMINAL_VISUAL_UPDATE=1 in env.

    Otherwise assert diff_ratio < tolerance, with a message that
    includes both paths so the developer can open them in meld/imv.
    """
    baseline = Path(baseline_dir) / f"{name}.png"
    if not baseline.exists() or update:
        Path(baseline_dir).mkdir(parents=True, exist_ok=True)
        Image.open(actual_path).save(baseline)
        return
    ratio = diff_ratio(actual_path, baseline, pixel_threshold)
    assert ratio < tolerance, (
        f"Visual diff for '{name}': {ratio:.2%} of pixels differ "
        f"(tolerance {tolerance:.2%}, threshold {pixel_threshold})\n"
        f"  baseline: {baseline}\n"
        f"  actual:   {actual_path}\n"
        f"  hint: BTERMINAL_VISUAL_UPDATE=1 pytest ... to accept new baseline"
    )
