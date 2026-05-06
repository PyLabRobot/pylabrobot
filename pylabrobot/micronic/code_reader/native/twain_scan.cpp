// Minimal TWAIN native-transfer scanner for the Avision AVA6PlusG source.
//
// This is intentionally independent of Micronic Code Reader. It talks to the
// installed TWAIN source manager and the Avision TWAIN data source, then saves
// the transferred DIB as a BMP.

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

#pragma pack(push, 2)
using TW_INT16 = int16_t;
using TW_UINT16 = uint16_t;
using TW_INT32 = int32_t;
using TW_UINT32 = uint32_t;
using TW_BOOL = uint16_t;
using TW_HANDLE = void*;
using TW_MEMREF = void*;

using TW_STR32 = char[34];

struct TW_VERSION {
  TW_UINT16 MajorNum;
  TW_UINT16 MinorNum;
  TW_UINT16 Language;
  TW_UINT16 Country;
  TW_STR32 Info;
};

struct TW_IDENTITY {
  TW_UINT32 Id;
  TW_VERSION Version;
  TW_UINT16 ProtocolMajor;
  TW_UINT16 ProtocolMinor;
  TW_UINT32 SupportedGroups;
  TW_STR32 Manufacturer;
  TW_STR32 ProductFamily;
  TW_STR32 ProductName;
};

struct TW_USERINTERFACE {
  TW_BOOL ShowUI;
  TW_BOOL ModalUI;
  TW_HANDLE hParent;
};

struct TW_EVENT {
  TW_MEMREF pEvent;
  TW_UINT16 TWMessage;
};

struct TW_PENDINGXFERS {
  TW_UINT16 Count;
  TW_UINT32 EOJ;
};

struct TW_CAPABILITY {
  TW_UINT16 Cap;
  TW_UINT16 ConType;
  TW_HANDLE hContainer;
};

struct TW_ONEVALUE {
  TW_UINT16 ItemType;
  TW_UINT32 Item;
};
#pragma pack(pop)

using DSMEntry = TW_UINT16(WINAPI*)(
  TW_IDENTITY* origin,
  TW_IDENTITY* dest,
  TW_UINT32 dg,
  TW_UINT16 dat,
  TW_UINT16 msg,
  TW_MEMREF data
);

static constexpr TW_UINT32 DG_CONTROL = 0x0001;
static constexpr TW_UINT32 DG_IMAGE = 0x0002;

static constexpr TW_UINT16 DAT_CAPABILITY = 0x0001;
static constexpr TW_UINT16 DAT_EVENT = 0x0002;
static constexpr TW_UINT16 DAT_IDENTITY = 0x0003;
static constexpr TW_UINT16 DAT_PARENT = 0x0004;
static constexpr TW_UINT16 DAT_PENDINGXFERS = 0x0005;
static constexpr TW_UINT16 DAT_USERINTERFACE = 0x0009;
static constexpr TW_UINT16 DAT_IMAGENATIVEXFER = 0x0104;

static constexpr TW_UINT16 MSG_GETFIRST = 0x0004;
static constexpr TW_UINT16 MSG_GETNEXT = 0x0005;
static constexpr TW_UINT16 MSG_OPENDSM = 0x0301;
static constexpr TW_UINT16 MSG_CLOSEDSM = 0x0302;
static constexpr TW_UINT16 MSG_OPENDS = 0x0401;
static constexpr TW_UINT16 MSG_CLOSEDS = 0x0402;
static constexpr TW_UINT16 MSG_DISABLEDS = 0x0501;
static constexpr TW_UINT16 MSG_ENABLEDS = 0x0502;
static constexpr TW_UINT16 MSG_PROCESSEVENT = 0x0601;
static constexpr TW_UINT16 MSG_ENDXFER = 0x0701;
static constexpr TW_UINT16 MSG_GET = 0x0001;
static constexpr TW_UINT16 MSG_SET = 0x0006;

static constexpr TW_UINT16 MSG_XFERREADY = 0x0101;
static constexpr TW_UINT16 MSG_CLOSEDSREQ = 0x0102;
static constexpr TW_UINT16 MSG_CLOSEDSOK = 0x0103;

static constexpr TW_UINT16 TWRC_SUCCESS = 0;
static constexpr TW_UINT16 TWRC_FAILURE = 1;
static constexpr TW_UINT16 TWRC_DSEVENT = 4;
static constexpr TW_UINT16 TWRC_NOTDSEVENT = 5;
static constexpr TW_UINT16 TWRC_XFERDONE = 6;
static constexpr TW_UINT16 TWRC_ENDOFLIST = 7;

static constexpr TW_UINT16 TWON_PROTOCOLMAJOR = 1;
static constexpr TW_UINT16 TWON_PROTOCOLMINOR = 9;
static constexpr TW_UINT16 TWON_ONEVALUE = 0x0005;

static constexpr TW_UINT16 TWTY_INT16 = 0x0001;
static constexpr TW_UINT16 TWTY_UINT16 = 0x0004;
static constexpr TW_UINT16 TWTY_FIX32 = 0x0007;

static constexpr TW_UINT16 CAP_XFERCOUNT = 0x0001;
static constexpr TW_UINT16 ICAP_PIXELTYPE = 0x0101;
static constexpr TW_UINT16 ICAP_XFERMECH = 0x0103;
static constexpr TW_UINT16 ICAP_XRESOLUTION = 0x1118;
static constexpr TW_UINT16 ICAP_YRESOLUTION = 0x1119;
static constexpr TW_UINT16 ICAP_BITDEPTH = 0x112b;

static constexpr TW_UINT16 TWPT_BW = 0;
static constexpr TW_UINT16 TWPT_GRAY = 1;
static constexpr TW_UINT16 TWPT_RGB = 2;
static constexpr TW_UINT16 TWSX_NATIVE = 0;

static HWND g_hwnd = nullptr;

static void copy_twstr(TW_STR32 target, const char* source) {
  std::memset(target, 0, sizeof(TW_STR32));
  std::strncpy(target, source, sizeof(TW_STR32) - 1);
}

static const char* rc_name(TW_UINT16 rc) {
  switch (rc) {
    case TWRC_SUCCESS: return "SUCCESS";
    case TWRC_FAILURE: return "FAILURE";
    case TWRC_DSEVENT: return "DSEVENT";
    case TWRC_NOTDSEVENT: return "NOTDSEVENT";
    case TWRC_XFERDONE: return "XFERDONE";
    case TWRC_ENDOFLIST: return "ENDOFLIST";
    default: return "OTHER";
  }
}

static bool write_bmp_from_dib(HGLOBAL h_dib, const char* output_path) {
  void* data = GlobalLock(h_dib);
  if (data == nullptr) {
    std::fprintf(stderr, "GlobalLock failed: %lu\n", GetLastError());
    return false;
  }

  auto* bih = static_cast<BITMAPINFOHEADER*>(data);
  if (bih->biSize < sizeof(BITMAPINFOHEADER)) {
    std::fprintf(stderr, "Unexpected DIB header size: %lu\n", static_cast<unsigned long>(bih->biSize));
    GlobalUnlock(h_dib);
    return false;
  }

  const DWORD color_count = bih->biClrUsed
    ? bih->biClrUsed
    : (bih->biBitCount <= 8 ? (1u << bih->biBitCount) : 0u);
  const DWORD palette_bytes = color_count * sizeof(RGBQUAD);
  const DWORD pixel_offset = sizeof(BITMAPFILEHEADER) + bih->biSize + palette_bytes;

  DWORD image_bytes = bih->biSizeImage;
  if (image_bytes == 0) {
    const DWORD width = static_cast<DWORD>(bih->biWidth < 0 ? -bih->biWidth : bih->biWidth);
    const DWORD height = static_cast<DWORD>(bih->biHeight < 0 ? -bih->biHeight : bih->biHeight);
    const DWORD row_bytes = ((width * bih->biBitCount + 31u) / 32u) * 4u;
    image_bytes = row_bytes * height;
  }

  BITMAPFILEHEADER bfh{};
  bfh.bfType = 0x4d42;
  bfh.bfOffBits = pixel_offset;
  bfh.bfSize = pixel_offset + image_bytes;

  HANDLE file = CreateFileA(
    output_path,
    GENERIC_WRITE,
    0,
    nullptr,
    CREATE_ALWAYS,
    FILE_ATTRIBUTE_NORMAL,
    nullptr
  );
  if (file == INVALID_HANDLE_VALUE) {
    std::fprintf(stderr, "CreateFile failed for %s: %lu\n", output_path, GetLastError());
    GlobalUnlock(h_dib);
    return false;
  }

  DWORD written = 0;
  const bool ok =
    WriteFile(file, &bfh, sizeof(bfh), &written, nullptr) &&
    WriteFile(file, data, bih->biSize + palette_bytes + image_bytes, &written, nullptr);
  CloseHandle(file);
  GlobalUnlock(h_dib);

  if (!ok) {
    std::fprintf(stderr, "WriteFile failed: %lu\n", GetLastError());
    return false;
  }

  std::printf(
    "saved %s width=%ld height=%ld bpp=%u bytes=%lu\n",
    output_path,
    static_cast<long>(bih->biWidth),
    static_cast<long>(bih->biHeight),
    static_cast<unsigned>(bih->biBitCount),
    static_cast<unsigned long>(bfh.bfSize)
  );
  return true;
}

static LRESULT CALLBACK wnd_proc(HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam) {
  return DefWindowProcA(hwnd, msg, wparam, lparam);
}

static TW_UINT32 fix32_item(TW_INT16 whole) {
  return static_cast<TW_UINT16>(whole);
}

static bool set_onevalue_cap(
  DSMEntry dsm_entry,
  TW_IDENTITY* app,
  TW_IDENTITY* source,
  TW_UINT16 cap_id,
  TW_UINT16 item_type,
  TW_UINT32 item
) {
  HGLOBAL handle = GlobalAlloc(GHND, sizeof(TW_ONEVALUE));
  if (handle == nullptr) {
    std::fprintf(stderr, "GlobalAlloc failed for cap %u\n", cap_id);
    return false;
  }
  auto* value = static_cast<TW_ONEVALUE*>(GlobalLock(handle));
  value->ItemType = item_type;
  value->Item = item;
  GlobalUnlock(handle);

  TW_CAPABILITY cap{};
  cap.Cap = cap_id;
  cap.ConType = TWON_ONEVALUE;
  cap.hContainer = handle;
  TW_UINT16 rc = dsm_entry(app, source, DG_CONTROL, DAT_CAPABILITY, MSG_SET, &cap);
  std::printf("SET cap=%u item=%lu rc=%s(%u)\n", cap_id, static_cast<unsigned long>(item), rc_name(rc), rc);
  GlobalFree(handle);
  return rc == TWRC_SUCCESS;
}

static HWND create_hidden_parent() {
  WNDCLASSA wc{};
  wc.lpfnWndProc = wnd_proc;
  wc.hInstance = GetModuleHandleA(nullptr);
  wc.lpszClassName = "MoleculesTwainHiddenParent";
  RegisterClassA(&wc);
  return CreateWindowExA(
    0,
    wc.lpszClassName,
    "Molecules TWAIN Hidden Parent",
    WS_OVERLAPPEDWINDOW,
    CW_USEDEFAULT,
    CW_USEDEFAULT,
    320,
    200,
    nullptr,
    nullptr,
    wc.hInstance,
    nullptr
  );
}

int main(int argc, char** argv) {
  const char* output_path = argc > 1 ? argv[1] : "twain_scan.bmp";
  const char* source_match = argc > 2 ? argv[2] : "AVA6PlusG";
  const DWORD timeout_ms = argc > 3 ? static_cast<DWORD>(std::strtoul(argv[3], nullptr, 10)) : 90000u;

  HMODULE twain = LoadLibraryA("TWAIN_32.DLL");
  if (twain == nullptr) {
    twain = LoadLibraryA("TWAINDSM.DLL");
  }
  if (twain == nullptr) {
    std::fprintf(stderr, "Could not load TWAIN source manager: %lu\n", GetLastError());
    return 2;
  }

  auto dsm_entry = reinterpret_cast<DSMEntry>(GetProcAddress(twain, "DSM_Entry"));
  if (dsm_entry == nullptr) {
    std::fprintf(stderr, "Could not find DSM_Entry: %lu\n", GetLastError());
    return 2;
  }

  g_hwnd = create_hidden_parent();
  if (g_hwnd == nullptr) {
    std::fprintf(stderr, "Could not create hidden parent window: %lu\n", GetLastError());
    return 2;
  }

  TW_IDENTITY app{};
  app.Id = 0;
  app.Version.MajorNum = 1;
  app.Version.MinorNum = 0;
  copy_twstr(app.Version.Info, "Molecules Alakascan");
  app.ProtocolMajor = TWON_PROTOCOLMAJOR;
  app.ProtocolMinor = TWON_PROTOCOLMINOR;
  app.SupportedGroups = DG_CONTROL | DG_IMAGE;
  copy_twstr(app.Manufacturer, "Molecules");
  copy_twstr(app.ProductFamily, "Alakascan");
  copy_twstr(app.ProductName, "alakascan-twain-scan");

  TW_UINT16 rc = dsm_entry(&app, nullptr, DG_CONTROL, DAT_PARENT, MSG_OPENDSM, &g_hwnd);
  std::printf("OPENDSM rc=%s(%u)\n", rc_name(rc), rc);
  if (rc != TWRC_SUCCESS) {
    return 3;
  }

  TW_IDENTITY source{};
  TW_IDENTITY selected{};
  bool found = false;
  rc = dsm_entry(&app, nullptr, DG_CONTROL, DAT_IDENTITY, MSG_GETFIRST, &source);
  while (rc == TWRC_SUCCESS) {
    std::printf("source: %s / %s / %s\n", source.Manufacturer, source.ProductFamily, source.ProductName);
    if (std::strstr(source.ProductName, source_match) != nullptr) {
      selected = source;
      found = true;
    }
    rc = dsm_entry(&app, nullptr, DG_CONTROL, DAT_IDENTITY, MSG_GETNEXT, &source);
  }
  if (!found) {
    std::fprintf(stderr, "No TWAIN source matching '%s'\n", source_match);
    dsm_entry(&app, nullptr, DG_CONTROL, DAT_PARENT, MSG_CLOSEDSM, &g_hwnd);
    return 4;
  }

  rc = dsm_entry(&app, nullptr, DG_CONTROL, DAT_IDENTITY, MSG_OPENDS, &selected);
  std::printf("OPENDS rc=%s(%u)\n", rc_name(rc), rc);
  if (rc != TWRC_SUCCESS) {
    dsm_entry(&app, nullptr, DG_CONTROL, DAT_PARENT, MSG_CLOSEDSM, &g_hwnd);
    return 5;
  }

  set_onevalue_cap(dsm_entry, &app, &selected, CAP_XFERCOUNT, TWTY_INT16, 1);
  set_onevalue_cap(dsm_entry, &app, &selected, ICAP_XFERMECH, TWTY_UINT16, TWSX_NATIVE);
  set_onevalue_cap(dsm_entry, &app, &selected, ICAP_PIXELTYPE, TWTY_UINT16, TWPT_GRAY);
  set_onevalue_cap(dsm_entry, &app, &selected, ICAP_BITDEPTH, TWTY_UINT16, 8);
  set_onevalue_cap(dsm_entry, &app, &selected, ICAP_XRESOLUTION, TWTY_FIX32, fix32_item(600));
  set_onevalue_cap(dsm_entry, &app, &selected, ICAP_YRESOLUTION, TWTY_FIX32, fix32_item(600));

  TW_USERINTERFACE ui{};
  ui.ShowUI = 0;
  ui.ModalUI = 0;
  ui.hParent = g_hwnd;
  rc = dsm_entry(&app, &selected, DG_CONTROL, DAT_USERINTERFACE, MSG_ENABLEDS, &ui);
  std::printf("ENABLEDS rc=%s(%u)\n", rc_name(rc), rc);
  if (rc != TWRC_SUCCESS) {
    dsm_entry(&app, &selected, DG_CONTROL, DAT_IDENTITY, MSG_CLOSEDS, &selected);
    dsm_entry(&app, nullptr, DG_CONTROL, DAT_PARENT, MSG_CLOSEDSM, &g_hwnd);
    return 6;
  }

  bool transferred = false;
  bool close_requested = false;
  const DWORD start = GetTickCount();
  while (!transferred && !close_requested && GetTickCount() - start < timeout_ms) {
    MSG msg;
    while (PeekMessageA(&msg, nullptr, 0, 0, PM_REMOVE)) {
      TW_EVENT event{};
      event.pEvent = &msg;
      event.TWMessage = 0;
      rc = dsm_entry(&app, &selected, DG_CONTROL, DAT_EVENT, MSG_PROCESSEVENT, &event);
      if (rc == TWRC_DSEVENT) {
        if (event.TWMessage == MSG_XFERREADY) {
          std::printf("XFERREADY\n");
          HGLOBAL h_dib = nullptr;
          rc = dsm_entry(&app, &selected, DG_IMAGE, DAT_IMAGENATIVEXFER, MSG_GET, &h_dib);
          std::printf("IMAGENATIVEXFER rc=%s(%u) handle=%p\n", rc_name(rc), rc, h_dib);
          if ((rc == TWRC_XFERDONE || rc == TWRC_SUCCESS) && h_dib != nullptr) {
            transferred = write_bmp_from_dib(h_dib, output_path);
            GlobalFree(h_dib);
          }
          TW_PENDINGXFERS pending{};
          TW_UINT16 erc = dsm_entry(&app, &selected, DG_CONTROL, DAT_PENDINGXFERS, MSG_ENDXFER, &pending);
          std::printf("ENDXFER rc=%s(%u) pending=%u\n", rc_name(erc), erc, pending.Count);
          break;
        }
        if (event.TWMessage == MSG_CLOSEDSREQ || event.TWMessage == MSG_CLOSEDSOK) {
          std::printf("close requested by source message=%u\n", event.TWMessage);
          close_requested = true;
        }
      } else {
        TranslateMessage(&msg);
        DispatchMessageA(&msg);
      }
    }
    Sleep(20);
  }

  if (!transferred) {
    std::fprintf(stderr, "Timed out or no transfer after %lu ms\n", static_cast<unsigned long>(timeout_ms));
  }

  dsm_entry(&app, &selected, DG_CONTROL, DAT_USERINTERFACE, MSG_DISABLEDS, &ui);
  dsm_entry(&app, &selected, DG_CONTROL, DAT_IDENTITY, MSG_CLOSEDS, &selected);
  dsm_entry(&app, nullptr, DG_CONTROL, DAT_PARENT, MSG_CLOSEDSM, &g_hwnd);
  DestroyWindow(g_hwnd);
  return transferred ? 0 : 7;
}
