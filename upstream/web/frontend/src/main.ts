import "./styles/styles.css";
import "./styles/accounts.css";
import "./styles/desktop-layout.css";
import "./styles/ui-refinement.css";
import "./styles/maintenance.css";
import "./styles/tobili.css";
import "./styles/cloud.css";
import "./styles/article.css";
import "./styles/settings-cards.css";
import { init } from "./bootstrap";
import { showToast } from "./shell/toast";

init().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  showToast(String(message || error), "error");
});
