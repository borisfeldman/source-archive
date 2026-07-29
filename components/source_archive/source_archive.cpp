#include "source_archive.h"

#include "esphome/core/log.h"
#include "esphome/core/progmem.h"

namespace esphome {
namespace source_archive {

static const char *const TAG = "source_archive";

class SourceArchiveRequestHandler final : public AsyncWebHandler {
 public:
  explicit SourceArchiveRequestHandler(SourceArchive *parent) : parent_(parent) {}

  bool canHandle(AsyncWebServerRequest *request) const override {
    if (request->method() != HTTP_GET)
      return false;

#ifdef USE_ESP32
    char url_buffer[AsyncWebServerRequest::URL_BUF_SIZE];
    return request->url_to(url_buffer) == this->parent_->get_download_path();
#else
    return request->url() == this->parent_->get_download_path().c_str();
#endif
  }

  void handleRequest(AsyncWebServerRequest *request) override {
#ifdef USE_ESP8266
    auto *response = request->beginResponse_P(200, "application/zip", this->parent_->get_data(),
                                              this->parent_->get_size());
#else
    auto *response = request->beginResponse(200, "application/zip", this->parent_->get_data(),
                                            this->parent_->get_size());
#endif
    response->addHeader(ESPHOME_F("Content-Disposition"), this->parent_->get_content_disposition().c_str());
    response->addHeader(ESPHOME_F("Cache-Control"), ESPHOME_F("no-store"));
    response->addHeader(ESPHOME_F("X-Content-Type-Options"), ESPHOME_F("nosniff"));
    request->send(response);
  }

 protected:
  SourceArchive *parent_;
};

void SourceArchive::setup() {
  auto *server = web_server_base::global_web_server_base;
  if (server == nullptr) {
    ESP_LOGE(TAG, "Web server not found");
    this->mark_failed();
    return;
  }

  server->add_handler(new SourceArchiveRequestHandler(this));  // NOLINT(cppcoreguidelines-owning-memory)
  // Repeats dump_config() late: that runs before a network log viewer can attach.
  this->set_timeout(15000, [this]() {
    ESP_LOGI(TAG, "Source archive ready: %s (%zu bytes) at %s", this->filename_.c_str(), this->size_,
             this->download_path_.c_str());
  });
}

void SourceArchive::dump_config() {
  ESP_LOGCONFIG(TAG, "Source Archive:");
  ESP_LOGCONFIG(TAG, "  Download path: %s", this->download_path_.c_str());
  ESP_LOGCONFIG(TAG, "  Filename: %s", this->filename_.c_str());
  ESP_LOGCONFIG(TAG, "  Size: %zu bytes", this->size_);
}

}  // namespace source_archive
}  // namespace esphome