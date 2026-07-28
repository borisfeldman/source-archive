#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <utility>

#include "esphome/components/web_server_base/web_server_base.h"
#include "esphome/core/component.h"

namespace esphome {
namespace source_archive {

class SourceArchive : public Component {
 public:
  void set_archive(const uint8_t *data, size_t size) {
    this->data_ = data;
    this->size_ = size;
  }
  void set_download_path(std::string download_path) { this->download_path_ = std::move(download_path); }
  void set_filename(std::string filename) {
    this->filename_ = std::move(filename);
    this->content_disposition_ = "attachment; filename=\"" + this->filename_ + "\"";
  }

  const uint8_t *get_data() const { return this->data_; }
  size_t get_size() const { return this->size_; }
  const std::string &get_download_path() const { return this->download_path_; }
  const std::string &get_content_disposition() const { return this->content_disposition_; }

  void setup() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::AFTER_WIFI; }

 protected:
  const uint8_t *data_{nullptr};
  size_t size_{0};
  std::string download_path_;
  std::string filename_;
  std::string content_disposition_;
};

}  // namespace source_archive
}  // namespace esphome