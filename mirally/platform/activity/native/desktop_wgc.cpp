// Product-owned persistent WGC sessions with on-demand CPU readback.
// JSONL carries metadata only; pixels are written to explicitly requested files.
#define NOMINMAX
#include <windows.h>
#include <shlwapi.h>
#include <d3d11.h>
#include <d3d11_4.h>
#include <dxgi.h>
#include <wincodec.h>
#include <windows.graphics.capture.interop.h>
#include <windows.graphics.directx.direct3d11.interop.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Foundation.Collections.h>
#include <winrt/Windows.Data.Json.h>
#include <winrt/Windows.Graphics.Capture.h>
#include <winrt/Windows.Graphics.DirectX.h>
#include <winrt/Windows.Graphics.DirectX.Direct3D11.h>
#include <chrono>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <iostream>
#include <string>
#include <thread>
#include <vector>
#include <condition_variable>
#include <map>
#include <memory>
#include <mutex>

using namespace winrt;
using namespace winrt::Windows::Graphics::Capture;
using namespace winrt::Windows::Graphics::DirectX;
using namespace winrt::Windows::Graphics::DirectX::Direct3D11;
using namespace winrt::Windows::Data::Json;
using IDirect3DDxgiInterfaceAccess = ::Windows::Graphics::DirectX::Direct3D11::IDirect3DDxgiInterfaceAccess;
static const char* g_stage = "startup";

static std::wstring wide(const char* value) {
  if (!value) return {};
  int size = MultiByteToWideChar(CP_UTF8, 0, value, -1, nullptr, 0);
  std::wstring result(size > 0 ? size : 0, L'\0');
  if (size > 0) {
    MultiByteToWideChar(CP_UTF8, 0, value, -1, result.data(), size);
    result.resize(size - 1);
  }
  return result;
}

static void write_png(const std::wstring& path, UINT width, UINT height, UINT stride, const BYTE* pixels) {
  g_stage = "wic_factory";
  com_ptr<IWICImagingFactory> factory;
  check_hresult(CoCreateInstance(CLSID_WICImagingFactory2, nullptr, CLSCTX_INPROC_SERVER,
                                 IID_PPV_ARGS(factory.put())));
  com_ptr<IWICStream> stream;
  g_stage = "wic_stream";
  check_hresult(factory->CreateStream(stream.put()));
  com_ptr<IStream> file_stream;
  check_hresult(SHCreateStreamOnFileEx(path.c_str(), STGM_CREATE | STGM_WRITE,
                                       FILE_ATTRIBUTE_NORMAL, TRUE, nullptr,
                                       file_stream.put()));
  check_hresult(stream->InitializeFromIStream(file_stream.get()));
  com_ptr<IWICBitmapEncoder> encoder;
  check_hresult(factory->CreateEncoder(GUID_ContainerFormatPng, nullptr, encoder.put()));
  g_stage = "wic_encoder";
  check_hresult(encoder->Initialize(stream.get(), WICBitmapEncoderNoCache));
  com_ptr<IWICBitmapFrameEncode> frame;
  com_ptr<IPropertyBag2> options;
  check_hresult(encoder->CreateNewFrame(frame.put(), options.put()));
  check_hresult(frame->Initialize(options.get()));
  check_hresult(frame->SetSize(width, height));
  WICPixelFormatGUID format = GUID_WICPixelFormat32bppBGRA;
  check_hresult(frame->SetPixelFormat(&format));
  if (format != GUID_WICPixelFormat32bppBGRA) throw hresult_error(WINCODEC_ERR_UNSUPPORTEDPIXELFORMAT);
  com_ptr<IWICBitmap> bitmap;
  check_hresult(factory->CreateBitmapFromMemory(width, height, GUID_WICPixelFormat32bppBGRA,
                                                stride, stride * height,
                                                const_cast<BYTE*>(pixels), bitmap.put()));
  g_stage = "wic_write";
  check_hresult(frame->WriteSource(bitmap.get(), nullptr));
  check_hresult(frame->Commit());
  check_hresult(encoder->Commit());
}

static void number(JsonObject const& object, const wchar_t* key, double value) {
  object.SetNamedValue(key, JsonValue::CreateNumberValue(value));
}
static void text(JsonObject const& object, const wchar_t* key, std::string const& value) {
  object.SetNamedValue(key, JsonValue::CreateStringValue(to_hstring(value)));
}
static void flag(JsonObject const& object, const wchar_t* key, bool value) {
  object.SetNamedValue(key, JsonValue::CreateBooleanValue(value));
}
static JsonObject failure(std::string const& reason) {
  JsonObject result;
  flag(result, L"captured", false);
  text(result, L"method", "wgc");
  text(result, L"error", reason);
  text(result, L"stage", g_stage);
  return result;
}
static void emit(JsonObject const& result) {
  std::cout << to_string(result.Stringify()) << std::endl;
}

struct GraphicsDevice {
  com_ptr<ID3D11Device> device;
  com_ptr<ID3D11DeviceContext> context;
  IDirect3DDevice direct{nullptr};
  std::string driver = "hardware";
  GraphicsDevice() {
    g_stage = "device";
    auto hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr,
      D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0, D3D11_SDK_VERSION,
      device.put(), nullptr, context.put());
    if (FAILED(hr)) {
      device = nullptr; context = nullptr; driver = "warp";
      check_hresult(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_WARP, nullptr,
        D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0, D3D11_SDK_VERSION,
        device.put(), nullptr, context.put()));
    }
    context.as<ID3D11Multithread>()->SetMultithreadProtected(TRUE);
    com_ptr<IInspectable> inspectable;
    check_hresult(CreateDirect3D11DeviceFromDXGIDevice(device.as<IDXGIDevice>().get(), inspectable.put()));
    direct = inspectable.as<IDirect3DDevice>();
  }
};

struct FrameState {
  std::mutex mutex;
  std::condition_variable changed;
  Direct3D11CaptureFrame latest{nullptr};
  winrt::Windows::Graphics::SizeInt32 size{};
  bool stopped = false;
  HRESULT error = S_OK;
  uint64_t frame_id = 0;
  uint64_t resize_count = 0;
};

class CaptureSession {
  std::shared_ptr<FrameState> state = std::make_shared<FrameState>();
  GraphicsCaptureItem item{nullptr};
  Direct3D11CaptureFramePool pool{nullptr};
  GraphicsCaptureSession session{nullptr};
  event_token arrived{}, closed{};
  bool subscribed = false;
public:
  uint64_t id;
  uint64_t readbacks = 0;
  std::string border_status = "unsupported";
  HRESULT border_hresult = S_OK;
  CaptureSession(GraphicsDevice const& graphics, bool monitor, uint64_t handle, uint64_t identity) : id(identity) {
    g_stage = "session";
    auto interop = get_activation_factory<GraphicsCaptureItem, IGraphicsCaptureItemInterop>();
    if (monitor) check_hresult(interop->CreateForMonitor(reinterpret_cast<HMONITOR>(handle), guid_of<GraphicsCaptureItem>(), put_abi(item)));
    else check_hresult(interop->CreateForWindow(reinterpret_cast<HWND>(handle), guid_of<GraphicsCaptureItem>(), put_abi(item)));
    state->size = item.Size();
    if (state->size.Width <= 0 || state->size.Height <= 0) throw hresult_error(E_INVALIDARG);
    pool = Direct3D11CaptureFramePool::CreateFreeThreaded(graphics.direct, DirectXPixelFormat::B8G8R8A8UIntNormalized, 3, state->size);
    session = pool.CreateCaptureSession(item);
    if (auto cursor = session.try_as<IGraphicsCaptureSession2>()) cursor.IsCursorCaptureEnabled(false);
    if (auto border = session.try_as<IGraphicsCaptureSession3>()) {
      try { border.IsBorderRequired(false); border_status = "disable_requested"; }
      catch (hresult_error const& e) { border_status = "request_failed"; border_hresult = e.code(); }
    }
    // MinUpdateInterval is optional. Older SDKs still support persistent WGC;
    // the ABI declaration guard comes from windows.graphics.capture.interop.h.
#if defined(____x_ABI_CWindows_CGraphics_CCapture_CIGraphicsCaptureSession5_INTERFACE_DEFINED__)
    if (auto interval = session.try_as<IGraphicsCaptureSession5>()) {
      try { interval.MinUpdateInterval(std::chrono::milliseconds(16)); } catch (hresult_error const&) {}
    }
#endif
    std::weak_ptr<FrameState> weak = state;
    auto direct = graphics.direct;
    arrived = pool.FrameArrived([weak, direct](auto const& sender, auto const&) {
      auto state = weak.lock(); if (!state) return;
      std::lock_guard<std::mutex> guard(state->mutex);
      if (state->stopped) return;
      try {
        while (auto frame = sender.TryGetNextFrame()) {
          auto size = frame.ContentSize();
          if (size.Width <= 0 || size.Height <= 0) { frame.Close(); continue; }
          if (size.Width != state->size.Width || size.Height != state->size.Height) {
            frame.Close();
            if (state->latest) { state->latest.Close(); state->latest = nullptr; }
            sender.Recreate(direct, DirectXPixelFormat::B8G8R8A8UIntNormalized, 3, size);
            state->size = size; ++state->resize_count;
            break;
          }
          if (state->latest) state->latest.Close();
          state->latest = frame; ++state->frame_id;
        }
      } catch (hresult_error const& e) { state->error = e.code(); state->stopped = true; }
      state->changed.notify_all();
    });
    closed = item.Closed([weak](auto const&, auto const&) {
      if (auto state = weak.lock()) {
        std::lock_guard<std::mutex> guard(state->mutex);
        state->stopped = true;
        state->changed.notify_all();
      }
    });
    subscribed = true;
    session.StartCapture();
  }
  ~CaptureSession() {
    { std::lock_guard<std::mutex> guard(state->mutex);
      state->stopped = true;
      if (state->latest) { try { state->latest.Close(); } catch (...) {} state->latest = nullptr; }
      state->changed.notify_all();
    }
    try { if (subscribed) { pool.FrameArrived(arrived); item.Closed(closed); } } catch (...) {}
    try { if (session) session.Close(); } catch (...) {}
    try { if (pool) pool.Close(); } catch (...) {}
  }
  bool ended() {
    std::lock_guard<std::mutex> guard(state->mutex);
    return state->stopped;
  }
  JsonObject capture(GraphicsDevice const& graphics, std::wstring const& output, int max_dimension, int timeout_ms) {
    g_stage = "frame";
    std::unique_lock<std::mutex> guard(state->mutex);
    state->changed.wait_for(guard, std::chrono::milliseconds(timeout_ms), [&] { return state->stopped || state->latest; });
    if (state->stopped) {
      if (FAILED(state->error)) throw hresult_error(state->error);
      return failure("target_closed");
    }
    if (!state->latest) return failure("wgc_no_frame");
    auto frame = state->latest;
    auto access = frame.Surface().as<IDirect3DDxgiInterfaceAccess>(); com_ptr<ID3D11Texture2D> texture;
    check_hresult(access->GetInterface(__uuidof(ID3D11Texture2D), texture.put_void()));
    D3D11_TEXTURE2D_DESC desc; texture->GetDesc(&desc);
    auto stagingdesc = desc; stagingdesc.Usage = D3D11_USAGE_STAGING; stagingdesc.BindFlags = 0; stagingdesc.MiscFlags = 0; stagingdesc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    g_stage = "readback";
    com_ptr<ID3D11Texture2D> staging; check_hresult(graphics.device->CreateTexture2D(&stagingdesc, nullptr, staging.put()));
    auto context = graphics.context;
    context->CopyResource(staging.get(), texture.get()); D3D11_MAPPED_SUBRESOURCE mapped;
    check_hresult(context->Map(staging.get(), 0, D3D11_MAP_READ, 0, &mapped));
    struct Unmap { ID3D11DeviceContext* context; ID3D11Texture2D* texture; ~Unmap() { context->Unmap(texture, 0); } } unmap{context.get(), staging.get()};
    int width = frame.ContentSize().Width, height = frame.ContentSize().Height;
    if (width <= 0 || height <= 0 || width > static_cast<int>(desc.Width) || height > static_cast<int>(desc.Height)) {
      throw hresult_error(E_INVALIDARG);
    }
    auto gray = [&](int x, int y) { auto p = static_cast<unsigned char*>(mapped.pData) + y * mapped.RowPitch + x * 4; return (77 * p[2] + 150 * p[1] + 29 * p[0]) >> 8; };
    int output_width = width;
    int output_height = height;
    if (max_dimension > 0 && std::max(width, height) > max_dimension) {
      double scale = static_cast<double>(max_dimension) / std::max(width, height);
      output_width = std::max(1, static_cast<int>(std::round(width * scale)));
      output_height = std::max(1, static_cast<int>(std::round(height * scale)));
    }
    std::vector<BYTE> pixels(static_cast<size_t>(output_width) * static_cast<size_t>(output_height) * 4);
    double sum = 0, sum2 = 0; int black = 0; unsigned long long hash = 0; int bit = 0;
    for (int y = 0; y < output_height; ++y) for (int x = 0; x < output_width; ++x) {
      int source_x = std::min(width - 1, (x * width) / output_width);
      int source_y = std::min(height - 1, (y * height) / output_height);
      auto source = static_cast<unsigned char*>(mapped.pData) + source_y * mapped.RowPitch + source_x * 4;
      auto destination = pixels.data() + (static_cast<size_t>(y) * output_width + x) * 4;
      memcpy(destination, source, 4);
    }
    for (int y = 0; y < 32; ++y) for (int x = 0; x < 32; ++x) { int v = gray((x * width) / 32, (y * height) / 32); sum += v; sum2 += v * v; if (v < 8) ++black; }
    for (int y = 0; y < 8; ++y) for (int x = 0; x < 8; ++x) { if (gray(((x + 1) * width) / 9, (y * height) / 8) > gray((x * width) / 9, (y * height) / 8)) hash |= (1ULL << bit); ++bit; }
    double mean = sum / 1024, sd = std::sqrt(std::max(0.0, sum2 / 1024 - mean * mean));
    JsonObject runtime;
    number(runtime, L"session_id", static_cast<double>(id));
    number(runtime, L"frame_id", static_cast<double>(state->frame_id));
    number(runtime, L"resize_count", static_cast<double>(state->resize_count));
    number(runtime, L"readbacks", static_cast<double>(++readbacks));
    text(runtime, L"border_status", border_status);
    number(runtime, L"border_hresult", border_hresult);
    // All expensive readback/encoding is request-driven; arrival only retains the latest GPU frame.
    guard.unlock();
    g_stage = "wic_begin";
    write_png(output, static_cast<UINT>(output_width), static_cast<UINT>(output_height), static_cast<UINT>(output_width * 4), pixels.data());
    JsonObject result;
    flag(result, L"captured", true); text(result, L"method", "wgc");
    JsonArray dimensions; dimensions.Append(JsonValue::CreateNumberValue(output_width)); dimensions.Append(JsonValue::CreateNumberValue(output_height));
    result.SetNamedValue(L"size", dimensions); result.SetNamedValue(L"runtime", runtime);
    number(result, L"mean", mean); number(result, L"std", sd); number(result, L"black_fraction", black / 1024.0);
    flag(result, L"near_black", black > 1003); text(result, L"dhash", std::to_string(hash));
    return result;
  }
};

class CaptureEngine {
  std::unique_ptr<GraphicsDevice> graphics;
  std::map<std::pair<bool, uint64_t>, std::unique_ptr<CaptureSession>> sessions;
  uint64_t next_session = 0;
public:
  JsonObject capture(bool monitor, uint64_t handle, std::wstring const& output, int dimension, int timeout) {
    auto began = std::chrono::steady_clock::now();
    JsonObject result;
    auto key = std::make_pair(monitor, handle);
    try {
      g_stage = "target";
      if (!GraphicsCaptureSession::IsSupported()) return failure("wgc_not_supported");
      std::map<uint64_t, bool> monitors;
      EnumDisplayMonitors(nullptr, nullptr, [](HMONITOR h, HDC, LPRECT, LPARAM value) -> BOOL {
        (*reinterpret_cast<std::map<uint64_t, bool>*>(value))[reinterpret_cast<uint64_t>(h)] = true;
        return TRUE;
      }, reinterpret_cast<LPARAM>(&monitors));
      for (auto it = sessions.begin(); it != sessions.end();) {
        if (it->second->ended() || (it->first.first && !monitors.count(it->first.second)) ||
            (!it->first.first && (monitor || it->first.second != handle))) it = sessions.erase(it);
        else ++it;
      }
      if ((monitor && !monitors.count(handle)) || (!monitor && (!IsWindow(reinterpret_cast<HWND>(handle)) || IsIconic(reinterpret_cast<HWND>(handle))))) {
        sessions.erase(key); return failure("target_unavailable");
      }
      if (graphics && FAILED(graphics->device->GetDeviceRemovedReason())) { sessions.clear(); graphics.reset(); }
      if (!graphics) graphics = std::make_unique<GraphicsDevice>();
      bool reused = sessions.count(key) != 0;
      if (!reused) {
        if (sessions.size() >= 16) return failure("session_limit");
        sessions[key] = std::make_unique<CaptureSession>(*graphics, monitor, handle, ++next_session);
      }
      result = sessions.at(key)->capture(*graphics, output, dimension, timeout);
      auto runtime = result.GetNamedObject(L"runtime", JsonObject());
      flag(runtime, L"persistent", true); flag(runtime, L"session_reused", reused);
      number(runtime, L"helper_pid", GetCurrentProcessId()); number(runtime, L"session_count", static_cast<double>(sessions.size()));
      text(runtime, L"device_driver", graphics->driver); result.SetNamedValue(L"runtime", runtime);
      if (!result.GetNamedBoolean(L"captured", false)) sessions.erase(key);
    } catch (hresult_error const& e) {
      sessions.erase(key); result = failure("native_hresult"); number(result, L"hresult", e.code());
    } catch (...) { sessions.erase(key); result = failure("native_error"); }
    number(result, L"elapsed_ms", std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - began).count());
    return result;
  }
};

int main(int argc, char** argv) {
  try {
    init_apartment(apartment_type::multi_threaded);
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    CaptureEngine engine;
    if (argc == 2 && std::string(argv[1]) == "--serve") {
      JsonObject ready; number(ready, L"protocol", 1); text(ready, L"event", "ready"); number(ready, L"pid", GetCurrentProcessId()); emit(ready);
      std::string line;
      while (std::getline(std::cin, line)) {
        JsonObject result;
        int request_id = 0;
        try {
          if (line.size() > 4096) throw hresult_error(E_INVALIDARG);
          auto request = JsonObject::Parse(to_hstring(line));
          request_id = static_cast<int>(request.GetNamedNumber(L"id"));
          auto type = request.GetNamedString(L"target_type");
          auto handle = std::stoull(to_string(request.GetNamedString(L"handle")));
          auto output = request.GetNamedString(L"output");
          if ((type != L"hwnd" && type != L"monitor") || output.empty() || !handle) throw hresult_error(E_INVALIDARG);
          result = engine.capture(type == L"monitor", handle, output.c_str(),
            std::clamp(static_cast<int>(request.GetNamedNumber(L"max_dimension", 1920)), 320, 8192),
            std::clamp(static_cast<int>(request.GetNamedNumber(L"timeout_ms", 1500)), 100, 30000));
        } catch (...) { result = failure("invalid_request"); }
        number(result, L"id", request_id); emit(result);
      }
      return 0; // EOF releases every capture session before the device.
    }
    std::string target; bool monitor = false; std::wstring output; int dimension = 1920;
    for (int i = 1; i + 1 < argc; ++i) {
      if (std::string(argv[i]) == "--hwnd") target = argv[++i];
      else if (std::string(argv[i]) == "--monitor") { target = argv[++i]; monitor = true; }
      else if (std::string(argv[i]) == "--output") output = wide(argv[++i]);
      else if (std::string(argv[i]) == "--max-dimension") dimension = std::stoi(argv[++i]);
    }
    if (target.empty() || output.empty()) { emit(failure("invalid_args")); return 2; }
    emit(engine.capture(monitor, std::stoull(target), output, std::clamp(dimension, 320, 8192), 1800));
  } catch (hresult_error const& e) {
    auto result = failure("native_hresult"); number(result, L"hresult", e.code()); emit(result); return 1;
  } catch (...) { emit(failure("native_error")); return 1; }
}
