import { MirallyFilePreviewModal } from "./file-preview-modal.ts";

if (!customElements.get("mirally-file-preview-modal")) {
  customElements.define("mirally-file-preview-modal", MirallyFilePreviewModal);
}
