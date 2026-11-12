"""SBB-Uhr: the Swiss railway clock as a small desktop window.

A Python translation of sbbUhr-1.3.js from the digital-clock folder of
https://github.com/sbb-design-systems/brand-elements, which carries
this notice:

    Digital SBB Uhr V 1.3 (marke@sbb.ch)
    All rights reserves by SBB AG, Switzerland.
    Apache-2.0 License

The face, the ticks and the hands use the geometry of the SVG drawings
in that script, and the hands move the same way:

- the second hand goes round in 58.5 s and waits at 12 for the rest of
  the minute,
- the minute and hour hands jump at the full minute and settle with a
  small elastic swing.

Modified by Niceno, 2026: translated from JavaScript to Python, drawn
with Pillow in a borderless always-on-top window (based on Win-Eyes).
"""

import math
import datetime
import functools
import ctypes
from ctypes import wintypes
from PIL import Image, ImageDraw, ImageChops

# ----------------------------------------------------------------------
# Settings (all dimensions are in pixels at 100 % display scaling;
# they grow with Windows scaling, e.g. by 10 % at 110 %).
# ----------------------------------------------------------------------

SIZE        = 160   # Width and height of the clock window
ZOOM        =   1   # Starting zoom; can be changed from the right-click menu
ZOOMS       = (0.5, 0.75, 1, 1.5, 2, 3, 4)   # Zoom choices in the menu
FRAME_MS    =  20   # How often the hands are updated (20 ms = 50 frames/s)
BEZEL       = True  # Grey ring around the face (the light version in the JS)
BEZEL_WIDTH =   6   # Its width in clock units; the face is 105 across (JS: 1.5)
INVERT      = False # Start with inverted colours (black face, white hands)

CYCLE_SECONDS = 58.5   # Time the second hand takes to go round
EASING_MS     = 2000   # Duration of the minute and hour hands' swing

FACE_COLOR   = (255, 255, 255)
BEZEL_COLOR  = (118, 118, 118)
TICK_COLOR   = ( 30,  30,  30)
HAND_COLOR   = (  0,   0,   0)
SECOND_COLOR = (235,   0,   0)   # Stays red when the colours are inverted

# ----------------------------------------------------------------------
# Clock geometry, copied from the SVG drawings in sbbUhr-1.3.js. All
# coordinates are in the SVG's 112 x 112 units, with the centre of the
# clock at (56, 56) and every hand drawn pointing to 12.
# ----------------------------------------------------------------------

CENTRE = 56

FACE_RADIUS  = 52.5   # White face
BEZEL_RADIUS = FACE_RADIUS + BEZEL_WIDTH   # Outer edge of the grey ring
MARGIN       = 2      # Transparent space around the clock

# Units across the window: 112 in the JS, more with a wider bezel.
UNITS = 2 * ((BEZEL_RADIUS if BEZEL else FACE_RADIUS) + MARGIN)

# Ticks: (width, inner radius, outer radius)
HOUR_TICK   = (3.5, 36.5, 48.5)
MINUTE_TICK = (1.4, 45.0, 48.5)

HOUR_HAND   = ((59.2, 68), (52.8, 68), (53.4, 24), (58.6, 24))
MINUTE_HAND = ((58.6, 68), (53.4, 68), (54.2, 10), (57.8, 10))

# Second hand: a thin stem ending in a disc ("Kelle").
SECOND_STEM = ((56.7, 72.5), (55.3, 72.5), (55.3, 24.8), (56.7, 24.8))
SECOND_DISC = ((56, 24.8), 5.25)   # Centre and radius

# ----------------------------------------------------------------------
# Win32 definitions needed for a true alpha-transparent window
# ----------------------------------------------------------------------

user32   = ctypes.windll.user32
gdi32    = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

WS_EX_LAYERED    = 0x00080000
WS_EX_TOPMOST    = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_POPUP         = 0x80000000

WM_DESTROY     = 0x0002
WM_LBUTTONDOWN = 0x0201
WM_RBUTTONUP   = 0x0205
WM_TIMER       = 0x0113
WM_DPICHANGED  = 0x02E0

SWP_NOZORDER   = 0x0004
SWP_NOACTIVATE = 0x0010

DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4

WM_NCLBUTTONDOWN = 0x00A1
HTCAPTION        = 2

MF_STRING       = 0x0000
MF_CHECKED      = 0x0008
MF_POPUP        = 0x0010
MF_SEPARATOR    = 0x0800
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD   = 0x0100

ULW_ALPHA      = 0x00000002
AC_SRC_OVER    = 0x00
AC_SRC_ALPHA   = 0x01
BI_RGB         = 0
DIB_RGB_COLORS = 0

ID_EXIT         = 1
ID_INVERT       = 2
ID_ZOOM_FIRST   = 100   # Zoom choice i gets command ID_ZOOM_FIRST + i
FRAME_TIMER_ID  = 1

IDC_ARROW = 32512

LRESULT = ctypes.c_ssize_t

user32.DefWindowProcW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM
]
user32.DefWindowProcW.restype = LRESULT


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class RGBQUAD(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", ctypes.c_ubyte),
        ("rgbGreen", ctypes.c_ubyte),
        ("rgbRed", ctypes.c_ubyte),
        ("rgbReserved", ctypes.c_ubyte),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", RGBQUAD * 1),
    ]


WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


# Full 64-bit prototypes for every Win32 call used below. Without these,
# ctypes assumes 32-bit int arguments and results, truncating handles.
UINT_PTR = ctypes.c_size_t

kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype  = wintypes.HMODULE

user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype  = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype  = ctypes.c_int
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype  = wintypes.BOOL
user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND,
    wintypes.HDC,
    ctypes.POINTER(wintypes.POINT),
    ctypes.POINTER(wintypes.SIZE),
    wintypes.HDC,
    ctypes.POINTER(wintypes.POINT),
    wintypes.COLORREF,
    ctypes.POINTER(BLENDFUNCTION),
    wintypes.DWORD,
]
user32.UpdateLayeredWindow.restype = wintypes.BOOL

user32.SendMessageW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]
user32.SendMessageW.restype = LRESULT

user32.CreatePopupMenu.argtypes = []
user32.CreatePopupMenu.restype  = wintypes.HMENU
user32.AppendMenuW.argtypes = [
    wintypes.HMENU, wintypes.UINT, UINT_PTR, wintypes.LPCWSTR
]
user32.AppendMenuW.restype = wintypes.BOOL
user32.TrackPopupMenu.argtypes = [
    wintypes.HMENU,
    wintypes.UINT,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    ctypes.c_void_p,
]
user32.TrackPopupMenu.restype = wintypes.BOOL
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.DestroyMenu.restype  = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype  = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype  = wintypes.BOOL
user32.ReleaseCapture.argtypes = []
user32.ReleaseCapture.restype  = wintypes.BOOL

user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
user32.LoadCursorW.restype  = wintypes.HANDLE
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype  = wintypes.ATOM
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HMENU,
    wintypes.HINSTANCE,
    wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype  = wintypes.BOOL
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype  = wintypes.BOOL
user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL

user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext.restype  = wintypes.BOOL
user32.GetDpiForWindow.argtypes = [wintypes.HWND]
user32.GetDpiForWindow.restype  = wintypes.UINT

user32.SetTimer.argtypes = [
    wintypes.HWND, UINT_PTR, wintypes.UINT, ctypes.c_void_p
]
user32.SetTimer.restype = UINT_PTR
user32.KillTimer.argtypes = [wintypes.HWND, UINT_PTR]
user32.KillTimer.restype  = wintypes.BOOL

user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT
]
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype  = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype  = LRESULT
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.PostQuitMessage.restype  = None

gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype  = wintypes.HDC
gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC,
    ctypes.POINTER(BITMAPINFO),
    wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p),
    wintypes.HANDLE,
    wintypes.DWORD,
]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype  = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype  = wintypes.BOOL
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype  = wintypes.BOOL


# ----------------------------------------------------------------------
# Time: where the hands point, as in sbbUhr-1.3.js. Angles are in
# degrees, clockwise from 12.
# ----------------------------------------------------------------------

def ease_out_elastic(t, start, change, duration):
    """The JS easing for the minute and hour hands: the hand is already
    at its new position at t = 0 and swings around it, dying out over
    the duration. t and duration are in milliseconds."""
    t /= duration
    if t < 1:
        return (change * 2 ** (-10 * t)
                * math.sin((t * duration - 2) * 2 * math.pi / 300) * 1.5
                + change + start)
    return start + change


def hand_angles(now):
    ms = now.second * 1000 + now.microsecond // 1000   # Into the minute

    # The JS adds the milliseconds as if the hand took 60 s to go round,
    # so the hand jumped slightly at every second; here it moves evenly.
    seconds = min(ms * 360 / (CYCLE_SECONDS * 1000), 360)

    minutes = ease_out_elastic(ms, now.minute * 6 - 6, 6, EASING_MS)
    hours = ease_out_elastic(ms, now.hour * 30 + now.minute / 2 - 0.5,
                             0.5, EASING_MS)
    return hours, minutes, seconds


# ----------------------------------------------------------------------
# Drawing: Pillow draws the clock several times larger and downsamples
# it smoothly, so every edge blends with whatever is behind the window.
# ----------------------------------------------------------------------

# Display scaling of the monitor the window is on (1.0 = 100 %, 96 DPI).
dpi_scale = 1.0

# Zoom chosen from the right-click menu.
zoom = ZOOM

# Inverted colours, toggled from the right-click menu.
inverted = INVERT

# How many times larger than on screen everything is drawn.
OVERSAMPLE = 4

# Hand angles drawn last, so nothing is redrawn while the hands stand
# still (the second hand waiting at 12, for instance).
current_angles = None


def scale():
    """All sizes above are given at 100 % and multiplied by this."""
    return dpi_scale * zoom


def clock_size():
    return round(SIZE * scale())


def shade(colour):
    """A face, bezel, tick or hand colour, inverted if chosen."""
    return tuple(255 - c for c in colour) if inverted else colour


def to_pixels(points, angle, size):
    """SVG units -> window pixels, turned clockwise by angle (degrees)
    around the centre of the clock."""
    k = size / UNITS
    cos = math.cos(math.radians(angle))
    sin = math.sin(math.radians(angle))
    result = []
    for x, y in points:
        dx = (x - CENTRE) * k
        dy = (y - CENTRE) * k
        result.append((size / 2 + dx * cos - dy * sin,
                       size / 2 + dx * sin + dy * cos))
    return result


def tick(width, inner, outer):
    """A tick pointing to 12, as a rectangle in SVG units."""
    half = width / 2
    top = CENTRE - outer
    bottom = CENTRE - inner
    return ((CENTRE - half, top), (CENTRE + half, top),
            (CENTRE + half, bottom), (CENTRE - half, bottom))


def premultiply_bgra(image):
    """UpdateLayeredWindow expects premultiplied BGRA pixels."""
    red, green, blue, alpha = image.split()
    return Image.merge("RGBA", (
        ImageChops.multiply(blue, alpha),
        ImageChops.multiply(green, alpha),
        ImageChops.multiply(red, alpha),
        alpha,
    ))


# The face never changes, so it is rendered once per scale and colour
# scheme. The arguments are not used directly, they only key the cache.
@functools.lru_cache(maxsize=None)
def make_face(current_scale, current_inverted):
    big = clock_size() * OVERSAMPLE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    k = big / UNITS

    def circle(radius, colour):
        r = radius * k
        draw.ellipse((big / 2 - r, big / 2 - r, big / 2 + r, big / 2 + r),
                     fill=colour)

    if BEZEL:
        circle(BEZEL_RADIUS, shade(BEZEL_COLOR))
    circle(FACE_RADIUS, shade(FACE_COLOR))

    for minute in range(60):
        shape = tick(*(HOUR_TICK if minute % 5 == 0 else MINUTE_TICK))
        draw.polygon(to_pixels(shape, minute * 6, big),
                     fill=shade(TICK_COLOR))

    # Shrink first, then premultiply: Pillow's reduce() already takes
    # transparency into account when averaging RGBA pixels.
    return premultiply_bgra(image.reduce(OVERSAMPLE))


def draw_hand(frame, polygons, discs, angle, colour):
    """Draw one hand onto the frame. The hand is drawn enlarged only
    inside its own bounding box, which keeps large clocks fast."""
    size = frame.width
    polygons = [to_pixels(p, angle, size) for p in polygons]
    discs = [(to_pixels([c], angle, size)[0], r * size / UNITS)
             for c, r in discs]

    xs = [x for p in polygons for x, _ in p]
    ys = [y for p in polygons for _, y in p]
    for (cx, cy), r in discs:
        xs += [cx - r, cx + r]
        ys += [cy - r, cy + r]

    left = max(0, math.floor(min(xs)))
    top = max(0, math.floor(min(ys)))
    right = min(size, math.ceil(max(xs)) + 1)
    bottom = min(size, math.ceil(max(ys)) + 1)

    o = OVERSAMPLE
    mask = Image.new("L", ((right - left) * o, (bottom - top) * o), 0)
    draw = ImageDraw.Draw(mask)

    for p in polygons:
        draw.polygon([((x - left) * o, (y - top) * o) for x, y in p],
                     fill=255)
    for (cx, cy), r in discs:
        x, y = (cx - left) * o, (cy - top) * o
        draw.ellipse((x - r * o, y - r * o, x + r * o, y + r * o), fill=255)

    # The hands lie on the opaque face, so the colour needs no
    # premultiplying; it only has to be given in BGR order.
    red, green, blue = colour
    frame.paste((blue, green, red, 255), (left, top, right, bottom),
                mask.reduce(o))


def make_frame(angles):
    hours, minutes, seconds = angles
    frame = make_face(scale(), inverted).copy()
    draw_hand(frame, [HOUR_HAND], [], hours, shade(HAND_COLOR))
    draw_hand(frame, [MINUTE_HAND], [], minutes, shade(HAND_COLOR))
    draw_hand(frame, [SECOND_STEM], [SECOND_DISC], seconds, SECOND_COLOR)
    return frame.tobytes()


def draw_clock(hwnd, angles):
    global current_angles
    current_angles = angles

    pixels = make_frame(angles)
    width = height = clock_size()

    screen_dc = user32.GetDC(None)
    memory_dc = gdi32.CreateCompatibleDC(screen_dc)

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = -height     # top-down bitmap
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB

    bits = ctypes.c_void_p()

    bitmap = gdi32.CreateDIBSection(
        memory_dc,
        ctypes.byref(bmi),
        DIB_RGB_COLORS,
        ctypes.byref(bits),
        None,
        0,
    )

    ctypes.memmove(bits, pixels, len(pixels))

    old_bitmap = gdi32.SelectObject(memory_dc, bitmap)

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))

    destination = wintypes.POINT(rect.left, rect.top)
    source = wintypes.POINT(0, 0)
    size = wintypes.SIZE(width, height)

    blend = BLENDFUNCTION(
        AC_SRC_OVER,
        0,
        255,
        AC_SRC_ALPHA,
    )

    user32.UpdateLayeredWindow(
        hwnd,
        screen_dc,
        ctypes.byref(destination),
        ctypes.byref(size),
        memory_dc,
        ctypes.byref(source),
        0,
        ctypes.byref(blend),
        ULW_ALPHA,
    )

    gdi32.SelectObject(memory_dc, old_bitmap)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(memory_dc)
    user32.ReleaseDC(None, screen_dc)


def current_hand_angles():
    # Steps finer than this are invisible even on the largest clock.
    step = 0.02
    return tuple(round(a / step) * step
                 for a in hand_angles(datetime.datetime.now()))


def update_clock(hwnd):
    angles = current_hand_angles()
    if angles != current_angles:
        draw_clock(hwnd, angles)


# ----------------------------------------------------------------------
# Native Windows mouse/menu handling
# ----------------------------------------------------------------------

def set_zoom(hwnd, new_zoom):
    """Resize the clock, keeping its centre on the same spot."""
    global zoom

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    centre_x = (rect.left + rect.right) // 2
    centre_y = (rect.top + rect.bottom) // 2

    zoom = new_zoom
    user32.SetWindowPos(
        hwnd,
        None,
        centre_x - clock_size() // 2,
        centre_y - clock_size() // 2,
        clock_size(),
        clock_size(),
        SWP_NOZORDER | SWP_NOACTIVATE,
    )
    draw_clock(hwnd, current_hand_angles())


def show_menu(hwnd):
    global inverted

    menu = user32.CreatePopupMenu()

    zoom_menu = user32.CreatePopupMenu()
    for index, choice in enumerate(ZOOMS):
        flags = MF_STRING | (MF_CHECKED if choice == zoom else 0)
        user32.AppendMenuW(zoom_menu, flags, ID_ZOOM_FIRST + index,
                           f"{choice:g}x")

    # The menu takes ownership of the submenu and destroys it with itself.
    user32.AppendMenuW(menu, MF_POPUP, zoom_menu, "Zoom")
    user32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if inverted else 0),
                       ID_INVERT, "Invert Colors")
    user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
    user32.AppendMenuW(menu, MF_STRING, ID_EXIT, "Exit")

    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))

    user32.SetForegroundWindow(hwnd)

    command = user32.TrackPopupMenu(
        menu,
        TPM_RIGHTBUTTON | TPM_RETURNCMD,
        point.x,
        point.y,
        0,
        hwnd,
        None,
    )

    user32.DestroyMenu(menu)

    if command == ID_EXIT:
        user32.DestroyWindow(hwnd)
    elif command == ID_INVERT:
        inverted = not inverted
        draw_clock(hwnd, current_hand_angles())
    elif ID_ZOOM_FIRST <= command < ID_ZOOM_FIRST + len(ZOOMS):
        set_zoom(hwnd, ZOOMS[command - ID_ZOOM_FIRST])


@WNDPROC
def window_proc(window, message, wparam, lparam):
    global dpi_scale

    if message == WM_LBUTTONDOWN:
        # Drag the clock around by any visible part.
        user32.ReleaseCapture()
        user32.SendMessageW(window, WM_NCLBUTTONDOWN, HTCAPTION, 0)
        return 0

    if message == WM_RBUTTONUP:
        show_menu(window)
        return 0

    if message == WM_TIMER:
        if wparam == FRAME_TIMER_ID:
            update_clock(window)
        return 0

    if message == WM_DPICHANGED:
        # Moved to a monitor with different scaling: resize and redraw.
        dpi_scale = (wparam & 0xFFFF) / 96
        suggested = wintypes.RECT.from_address(lparam)
        user32.SetWindowPos(
            window,
            None,
            suggested.left,
            suggested.top,
            clock_size(),
            clock_size(),
            SWP_NOZORDER | SWP_NOACTIVATE,
        )
        draw_clock(window, current_hand_angles())
        return 0

    if message == WM_DESTROY:
        user32.KillTimer(window, FRAME_TIMER_ID)
        user32.PostQuitMessage(0)
        return 0

    return user32.DefWindowProcW(window, message, wparam, lparam)


# ----------------------------------------------------------------------
# Create the small layered window
# ----------------------------------------------------------------------

def main():
    global dpi_scale

    hinstance = kernel32.GetModuleHandleW(None)
    class_name = "SBBUhrWindow"

    window_class = WNDCLASSW()
    window_class.lpfnWndProc = window_proc
    window_class.hInstance = hinstance
    window_class.hCursor = user32.LoadCursorW(
        None,
        ctypes.c_void_p(IDC_ARROW)
    )
    window_class.lpszClassName = class_name

    user32.RegisterClassW(ctypes.byref(window_class))

    # Handle display scaling ourselves, so Windows does not blur the
    # window by stretching it. Must happen before the window is created.
    user32.SetProcessDpiAwarenessContext(
        DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)

    hwnd = user32.CreateWindowExW(
        WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW,
        class_name,
        "SBB-Uhr",
        WS_POPUP,
        100,
        100,
        SIZE,
        SIZE,
        None,
        None,
        hinstance,
        None,
    )

    dpi_scale = user32.GetDpiForWindow(hwnd) / 96
    user32.SetWindowPos(hwnd, None, 100, 100, clock_size(), clock_size(),
                        SWP_NOZORDER | SWP_NOACTIVATE)

    draw_clock(hwnd, current_hand_angles())

    user32.ShowWindow(hwnd, 5)
    user32.SetTimer(hwnd, FRAME_TIMER_ID, FRAME_MS, None)

    # Windows message loop.
    message = wintypes.MSG()

    while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(message))
        user32.DispatchMessageW(ctypes.byref(message))


if __name__ == "__main__":
    main()
