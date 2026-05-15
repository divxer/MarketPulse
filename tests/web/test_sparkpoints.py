"""Unit tests for the _sparkpoints Jinja filter."""
from marketpulse.web.main import _sparkpoints


def test_empty_returns_empty_string():
    assert _sparkpoints([], 56, 20) == ""
    assert _sparkpoints(None, 56, 20) == ""


def test_single_value_returns_empty_string():
    """Need at least 2 points to draw a line."""
    assert _sparkpoints([5.0], 56, 20) == ""


def test_two_values_render_correct_endpoints():
    """[1.0, 10.0] with (56, 20): first at (0, 20), last at (56, 0)."""
    points = _sparkpoints([1.0, 10.0], 56, 20)
    parts = points.split()
    assert len(parts) == 2
    x0, y0 = map(float, parts[0].split(","))
    x1, y1 = map(float, parts[1].split(","))
    assert x0 == 0.0
    assert y0 == 20.0  # min value → bottom
    assert x1 == 56.0
    assert y1 == 0.0   # max value → top


def test_flat_line_renders_horizontal_midline():
    """All-equal values → horizontal line at height/2."""
    points = _sparkpoints([5.0, 5.0, 5.0], 56, 20)
    parts = points.split()
    assert len(parts) == 3
    for part in parts:
        _, y = map(float, part.split(","))
        assert y == 10.0  # height/2


def test_normalizes_to_dimensions():
    """4 evenly-spaced x values; y inverted (higher value → smaller y)."""
    points = _sparkpoints([1.0, 2.0, 3.0, 4.0], 60, 30)
    parts = points.split()
    assert len(parts) == 4
    xs = [float(p.split(",")[0]) for p in parts]
    ys = [float(p.split(",")[1]) for p in parts]
    # Evenly spaced x: 0, 20, 40, 60
    assert xs == [0.0, 20.0, 40.0, 60.0]
    # Y inverted: first (lowest value) → height=30, last (highest) → 0
    assert ys[0] == 30.0
    assert ys[-1] == 0.0
    # Strictly decreasing (since values strictly increasing)
    assert ys[0] > ys[1] > ys[2] > ys[3]


def test_handles_negative_values():
    """Negative values should normalize correctly."""
    points = _sparkpoints([-2.0, 0.0, 2.0], 56, 20)
    parts = points.split()
    assert len(parts) == 3
    ys = [float(p.split(",")[1]) for p in parts]
    # -2 is min → y=20; 2 is max → y=0; 0 is midway → y=10
    assert ys[0] == 20.0
    assert ys[1] == 10.0
    assert ys[2] == 0.0
