"""CPU-Temp: the CPU temperature on a small round gauge, in the style of
a classic car's temperature gauge, as a desktop window.

The temperature comes from Windows' performance counter "Thermal Zone
Information": the zone the firmware calls CPUZ, as HP's do, or else
the warmest zone there is.  It needs no driver and no administrator
rights.  If the computer has no zones, the needle turns grey and rests
at the bottom of the scale.

The window (borderless, on top, alpha-blended, remembering where it
was) comes from SBB-Uhr and Win-Eyes.
"""

import os
import json
import math
import time
import functools
import ctypes
from ctypes import wintypes
from PIL import Image, ImageDraw, ImageFont, ImageChops

# ----------------------------------------------------------------------
# Settings (all dimensions are in pixels at 100 % display scaling;
# they grow with Windows scaling, e.g. by 10 % at 110 %).
# ----------------------------------------------------------------------

SIZE      = 160   # Width and height of the gauge window
ZOOM      =   1   # Starting zoom; can be changed from the right-click menu
ZOOMS     = (0.5, 0.75, 1, 1.5, 2, 3, 4)   # Zoom choices in the menu
INVERT    = False # Start with inverted colours (white face, black ticks)
START_X   = 410   # Starting position: right of Net-Eye, Win-Eyes and
START_Y   =  10   # SBB-Uhr (10 + 70 + 10 + 140 + 10 + 160 + 10 = 410)

READ_MS   = 1000  # How often the temperature is read
FRAME_MS  =   20  # How often the needle moves, while it moves
GLIDE_MS  =  400  # How quickly the needle follows: the time it takes to
                  # cover about two thirds of the way to a new reading

ZONE      = "cpuz"   # Thermal zone to show (its name ends with this);
                     # if there is none, the warmest zone is shown

T_MIN     =  30   # Scale of the gauge, in degrees Celsius
T_MAX     = 100
T_RED     =  90   # Ticks from here up are red (the i5-8350U slows
                  # itself down at 100)
ANGLE     = 135   # The scale runs from -ANGLE to +ANGLE degrees
                  # around 12 o'clock

# SBB-Uhr's colours inverted, so that the black gauge matches SBB-Uhr
# inverted, and the gauge inverted (white) matches SBB-Uhr as it is
FACE_COLOR   = (  0,   0,   0)   # SBB-Uhr: 255
BEZEL_COLOR  = (137, 137, 137)   # SBB-Uhr: 118
TICK_COLOR   = (225, 225, 225)   # Ticks, numbers and labels; SBB-Uhr: 30
HUB_COLOR    = (137, 137, 137)   # As the bezel
RED_COLOR    = (235,   0,  30)   # Red ticks
NEEDLE_COLOR = (235,   0,   0)   # Stays as it is when inverted
NO_READING   = (137, 137, 137)   # Needle with no reading (as the bezel)

FONT      = "bahnschrift.ttf"    # Comes with Windows 10 and 11

# ----------------------------------------------------------------------
# Gauge geometry, in units of a 120 x 120 square, with the centre of
# the gauge at (60, 60).  Ticks and the needle are drawn pointing to
# 12, then turned to their place on the scale.
# ----------------------------------------------------------------------

UNITS  = 120
CENTRE = 60

# The proportions of SBB-Uhr (its face, ring and ticks in its 121
# units), scaled to these 120 units
BEZEL_RADIUS = 58     # Outer edge of the grey ring
FACE_RADIUS  = 52     # Black face

# Ticks: (width, inner radius, outer radius)
MAJOR_TICK = (2.6, 38.0, 48.0)   # Every 10 degrees, with a number
MINOR_TICK = (1.6, 42.0, 48.0)   # Every 5 degrees

NUMBER_RADIUS = 29.5  # Where the numbers' centres are
NUMBER_SIZE   = 10    # Height of the numbers
LABEL_SIZE    =  9    # Height of "°C" and "CPU"

# Needle: a tapering bar with a short tail behind the hub
NEEDLE = ((58.4, 68), (61.6, 68), (60.6, 13), (59.4, 13))
HUB    = ((60, 60), 8.5)   # Centre and radius

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
WM_EXITSIZEMOVE = 0x0232

SWP_NOZORDER   = 0x0004
SWP_NOACTIVATE = 0x0010

DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4

MONITOR_DEFAULTTONULL = 0

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
READ_TIMER_ID   = 2

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

user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype  = wintypes.HANDLE

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
# Reading the temperature: Windows' performance counter "Thermal Zone
# Information" gives every thermal zone's temperature in kelvin; the
# zone whose name ends with ZONE is shown, or else the warmest zone
# (computers name their zones differently: HP's CPU zone is CPUZ, a
# Dell Wyse 5070 has only TZ01).  Zones the firmware leaves empty read
# 273 K (0 degrees Celsius) and are ignored.
# ----------------------------------------------------------------------

pdh = ctypes.windll.pdh

PDH_FMT_DOUBLE = 0x00000200
PDH_MORE_DATA  = 0x800007D2
TEMPERATURE    = r"\Thermal Zone Information(*)\Temperature"


class PDH_VALUE(ctypes.Union):
    _fields_ = [
        ("longValue", ctypes.c_long),
        ("doubleValue", ctypes.c_double),
        ("largeValue", ctypes.c_longlong),
        ("AnsiStringValue", ctypes.c_char_p),
        ("WideStringValue", ctypes.c_wchar_p),
    ]


class PDH_FMT_COUNTERVALUE(ctypes.Structure):
    _fields_ = [
        ("CStatus", wintypes.DWORD),
        ("value", PDH_VALUE),
    ]


class PDH_FMT_COUNTERVALUE_ITEM_W(ctypes.Structure):
    _fields_ = [
        ("szName", ctypes.c_wchar_p),
        ("FmtValue", PDH_FMT_COUNTERVALUE),
    ]


pdh.PdhOpenQueryW.argtypes = [
    wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)
]
pdh.PdhOpenQueryW.restype = ctypes.c_ulong
pdh.PdhAddEnglishCounterW.argtypes = [
    wintypes.HANDLE, wintypes.LPCWSTR, ctypes.c_size_t,
    ctypes.POINTER(wintypes.HANDLE)
]
pdh.PdhAddEnglishCounterW.restype = ctypes.c_ulong
pdh.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
pdh.PdhCollectQueryData.restype  = ctypes.c_ulong
pdh.PdhGetFormattedCounterArrayW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p
]
pdh.PdhGetFormattedCounterArrayW.restype = ctypes.c_ulong

# The query and its counter, once opened
counter = None


def open_counter():
    global counter
    query = wintypes.HANDLE()
    handle = wintypes.HANDLE()
    if pdh.PdhOpenQueryW(None, 0, ctypes.byref(query)) != 0:
        return
    if pdh.PdhAddEnglishCounterW(query, TEMPERATURE, 0,
                                 ctypes.byref(handle)) != 0:
        return
    counter = (query, handle)


def read_temperature():
    """The temperature of the zone ZONE, or else of the warmest zone, in
    degrees Celsius; None if no zone can be read."""
    if counter is None:
        return None
    query, handle = counter
    if pdh.PdhCollectQueryData(query) != 0:
        return None

    # Ask for the size of the array first, then fetch it.
    size = wintypes.DWORD(0)
    count = wintypes.DWORD(0)
    status = pdh.PdhGetFormattedCounterArrayW(
        handle, PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count),
        None)
    if status != PDH_MORE_DATA:
        return None
    buffer = ctypes.create_string_buffer(size.value)
    status = pdh.PdhGetFormattedCounterArrayW(
        handle, PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count),
        buffer)
    if status != 0:
        return None

    items = ctypes.cast(buffer,
                        ctypes.POINTER(PDH_FMT_COUNTERVALUE_ITEM_W))
    warmest = None
    for index in range(count.value):
        item = items[index]
        kelvin = item.FmtValue.value.doubleValue
        if item.FmtValue.CStatus != 0 or kelvin <= 273.5:
            continue   # Empty zone
        celsius = kelvin - 273.15
        if item.szName.lower().endswith(ZONE.lower()):
            return celsius
        if warmest is None or celsius > warmest:
            warmest = celsius
    return warmest


# ----------------------------------------------------------------------
# Drawing: Pillow draws the gauge several times larger and downsamples
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

# The last reading (None if there is none), the temperature the needle
# shows while it glides towards it, and when it last moved.  The needle
# starts at the bottom of the scale and swings up, like in a car.
temperature = None
shown = T_MIN
last_frame = time.monotonic()

# Needle angle drawn last, so nothing is redrawn while it stands still.
current_angle = None


def scale():
    """All sizes above are given at 100 % and multiplied by this."""
    return dpi_scale * zoom


def gauge_size():
    return round(SIZE * scale())


def shade(colour):
    """A face, bezel, tick or hub colour, inverted if chosen."""
    if inverted:
        return tuple(255 - c for c in colour)
    return colour


def angle_of(celsius):
    """Where the needle points for a temperature, in degrees clockwise
    from 12; below or above the scale it rests at its end."""
    celsius = max(T_MIN, min(T_MAX, celsius))
    return -ANGLE + (celsius - T_MIN) / (T_MAX - T_MIN) * 2 * ANGLE


def to_pixels(points, angle, size):
    """Gauge units -> window pixels, turned clockwise by angle (degrees)
    around the centre of the gauge."""
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
    """A tick pointing to 12, as a rectangle in gauge units."""
    half = width / 2
    top = CENTRE - outer
    bottom = CENTRE - inner
    return ((CENTRE - half, top), (CENTRE + half, top),
            (CENTRE + half, bottom), (CENTRE - half, bottom))


def font(height, size):
    """The font at a height in gauge units, for a gauge size pixels
    across; Pillow's own font if FONT isn't there."""
    pixels = max(1, round(height * size / UNITS))
    try:
        return ImageFont.truetype(FONT, pixels)
    except OSError:
        return ImageFont.load_default(pixels)


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
    big = gauge_size() * OVERSAMPLE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    k = big / UNITS

    def circle(radius, colour):
        r = radius * k
        draw.ellipse((big / 2 - r, big / 2 - r, big / 2 + r, big / 2 + r),
                     fill=colour)

    circle(BEZEL_RADIUS, shade(BEZEL_COLOR))
    circle(FACE_RADIUS, shade(FACE_COLOR))

    numbers = font(NUMBER_SIZE, big)
    for celsius in range(T_MIN, T_MAX + 1, 5):
        colour = shade(TICK_COLOR)
        if celsius >= T_RED:
            colour = RED_COLOR
        major = celsius % 10 == 0
        if major:
            shape = tick(*MAJOR_TICK)
        else:
            shape = tick(*MINOR_TICK)
        angle = angle_of(celsius)
        draw.polygon(to_pixels(shape, angle, big), fill=colour)
        if major:
            spot = to_pixels([(CENTRE, CENTRE - NUMBER_RADIUS)], angle, big)
            draw.text(spot[0], str(celsius), font=numbers, fill=colour,
                      anchor="mm")

    labels = font(LABEL_SIZE, big)
    draw.text((big / 2, (CENTRE + 17) * k), "°C", font=labels,
              fill=shade(TICK_COLOR), anchor="mm")
    draw.text((big / 2, (CENTRE + 38) * k), "CPU", font=labels,
              fill=shade(TICK_COLOR), anchor="mm")

    # Shrink first, then premultiply: Pillow's reduce() already takes
    # transparency into account when averaging RGBA pixels.
    return premultiply_bgra(image.reduce(OVERSAMPLE))


def draw_hand(frame, polygons, discs, angle, colour):
    """Draw the needle or the hub onto the frame. It is drawn enlarged
    only inside its own bounding box, which keeps large gauges fast."""
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

    # The needle and hub lie on the opaque face, so the colour needs no
    # premultiplying; it only has to be given in BGR order.
    red, green, blue = colour
    frame.paste((blue, green, red, 255), (left, top, right, bottom),
                mask.reduce(o))


def make_frame(angle):
    frame = make_face(scale(), inverted).copy()
    colour = NEEDLE_COLOR
    if temperature is None:
        colour = NO_READING
    draw_hand(frame, [NEEDLE], [], angle, colour)
    draw_hand(frame, [], [HUB], 0, shade(HUB_COLOR))
    return frame.tobytes()


def draw_gauge(hwnd, angle):
    global current_angle
    current_angle = angle

    pixels = make_frame(angle)
    width = height = gauge_size()

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


def needle_angle():
    # Steps finer than this are invisible even on the largest gauge.
    step = 0.05
    return round(angle_of(shown) / step) * step


def target():
    """Where the needle is heading: the last reading, or the bottom of
    the scale if there is none."""
    if temperature is None:
        return T_MIN
    return temperature


def new_reading(hwnd):
    """Read the temperature; if the needle has to move, start the frame
    timer (it stops itself once the needle has arrived)."""
    global temperature, last_frame
    had_reading = temperature is not None
    temperature = read_temperature()

    # The needle's colour tells whether there is a reading
    if had_reading != (temperature is not None):
        draw_gauge(hwnd, needle_angle())

    if needle_angle() != round(angle_of(target()) / 0.05) * 0.05:
        last_frame = time.monotonic()
        user32.SetTimer(hwnd, FRAME_TIMER_ID, FRAME_MS, None)


def move_needle(hwnd):
    """Glide the needle towards the last reading: each frame covers a
    part of the way left, so it slows down as it arrives."""
    global shown, last_frame

    now = time.monotonic()
    elapsed_ms = (now - last_frame) * 1000
    last_frame = now

    shown += (target() - shown) * (1 - math.exp(-elapsed_ms / GLIDE_MS))

    # Close enough: put it there and let the frame timer rest
    if abs(angle_of(target()) - angle_of(shown)) < 0.05:
        shown = target()
        user32.KillTimer(hwnd, FRAME_TIMER_ID)

    angle = needle_angle()
    if angle != current_angle:
        draw_gauge(hwnd, angle)


# ----------------------------------------------------------------------
# Remembering the window: its position, zoom and colours are saved
# after every change, and read at the start, so the gauge comes back
# as it was after a reboot.  Exit from the menu deletes them, so the
# next start is at the starting position, zoom and colours again.
# ----------------------------------------------------------------------

SETTINGS_FILE = os.path.join(os.environ.get("APPDATA", "."),
                             "CPU-Temp", "settings.json")


def load_settings():
    """The settings saved last, or {} if there are none."""
    try:
        with open(SETTINGS_FILE) as file:
            settings = json.load(file)
    except (OSError, ValueError):
        return {}
    if not isinstance(settings, dict):
        return {}
    return settings


def save_settings(hwnd):
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    settings = {"x": rect.left, "y": rect.top, "zoom": zoom,
                "inverted": inverted}

    # Write a new file and swap it in, so a crash or a power cut while
    # writing never leaves half a file behind.
    temporary = SETTINGS_FILE + ".tmp"
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        with open(temporary, "w") as file:
            json.dump(settings, file)
        os.replace(temporary, SETTINGS_FILE)
    except OSError:
        pass


def forget_settings():
    try:
        os.remove(SETTINGS_FILE)
    except OSError:
        pass


def on_a_monitor(x, y):
    """True if the point (in screen pixels) is on a monitor; a saved
    position may be on one that is no longer connected."""
    point = wintypes.POINT(x, y)
    return bool(user32.MonitorFromPoint(point, MONITOR_DEFAULTTONULL))


# ----------------------------------------------------------------------
# Native Windows mouse/menu handling
# ----------------------------------------------------------------------

def set_zoom(hwnd, new_zoom):
    """Resize the gauge, keeping the upper left corner on the same spot."""
    global zoom

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))

    zoom = new_zoom
    user32.SetWindowPos(
        hwnd,
        None,
        rect.left,
        rect.top,
        gauge_size(),
        gauge_size(),
        SWP_NOZORDER | SWP_NOACTIVATE,
    )
    draw_gauge(hwnd, needle_angle())
    save_settings(hwnd)


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
        forget_settings()
        user32.DestroyWindow(hwnd)
    elif command == ID_INVERT:
        inverted = not inverted
        draw_gauge(hwnd, needle_angle())
        save_settings(hwnd)
    elif ID_ZOOM_FIRST <= command < ID_ZOOM_FIRST + len(ZOOMS):
        set_zoom(hwnd, ZOOMS[command - ID_ZOOM_FIRST])


@WNDPROC
def window_proc(window, message, wparam, lparam):
    global dpi_scale

    if message == WM_LBUTTONDOWN:
        # Drag the gauge around by any visible part.
        user32.ReleaseCapture()
        user32.SendMessageW(window, WM_NCLBUTTONDOWN, HTCAPTION, 0)
        return 0

    if message == WM_EXITSIZEMOVE:
        # A drag has ended.
        save_settings(window)
        return 0

    if message == WM_RBUTTONUP:
        show_menu(window)
        return 0

    if message == WM_TIMER:
        if wparam == FRAME_TIMER_ID:
            move_needle(window)
        elif wparam == READ_TIMER_ID:
            new_reading(window)
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
            gauge_size(),
            gauge_size(),
            SWP_NOZORDER | SWP_NOACTIVATE,
        )
        draw_gauge(window, needle_angle())
        save_settings(window)
        return 0

    if message == WM_DESTROY:
        user32.KillTimer(window, FRAME_TIMER_ID)
        user32.KillTimer(window, READ_TIMER_ID)
        user32.PostQuitMessage(0)
        return 0

    return user32.DefWindowProcW(window, message, wparam, lparam)


# ----------------------------------------------------------------------
# Create the small layered window
# ----------------------------------------------------------------------

def main():
    global dpi_scale, zoom, inverted

    hinstance = kernel32.GetModuleHandleW(None)
    class_name = "CPUTempWindow"

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

    # Where and how the gauge was, if that was saved and its centre is
    # still on a monitor; else the starting position, zoom and colours.
    saved = load_settings()
    if saved.get("zoom") in ZOOMS:
        zoom = saved["zoom"]
    if isinstance(saved.get("inverted"), bool):
        inverted = saved["inverted"]
    x = saved.get("x")
    y = saved.get("y")
    restored = (isinstance(x, int) and isinstance(y, int)
                and on_a_monitor(x + SIZE // 2, y + SIZE // 2))
    if not restored:
        x = START_X
        y = START_Y

    hwnd = user32.CreateWindowExW(
        WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW,
        class_name,
        "CPU-Temp",
        WS_POPUP,
        x,
        y,
        SIZE,
        SIZE,
        None,
        None,
        hinstance,
        None,
    )

    # The starting position grows with the display scaling, like the
    # sizes.
    dpi_scale = user32.GetDpiForWindow(hwnd) / 96
    if not restored:
        x = round(START_X * dpi_scale)
        y = round(START_Y * dpi_scale)
    user32.SetWindowPos(hwnd, None, x, y, gauge_size(), gauge_size(),
                        SWP_NOZORDER | SWP_NOACTIVATE)

    # The needle starts at the bottom and swings up to the first
    # reading.
    open_counter()
    draw_gauge(hwnd, needle_angle())

    user32.ShowWindow(hwnd, 5)
    new_reading(hwnd)
    user32.SetTimer(hwnd, READ_TIMER_ID, READ_MS, None)

    # Windows message loop.
    message = wintypes.MSG()

    while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(message))
        user32.DispatchMessageW(ctypes.byref(message))


if __name__ == "__main__":
    main()
