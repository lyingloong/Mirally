import { QuestionPage } from "./question-page.ts";

if (!customElements.get("mirally-question-page")) {
  customElements.define("mirally-question-page", QuestionPage);
}
