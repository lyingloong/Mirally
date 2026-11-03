import { releases } from "./releases.js";
import ReleasePage from "./ReleasePage.jsx";
import UpdatesIndex from "./UpdatesIndex.jsx";

function findRelease() {
  const segments = window.location.pathname.split("/").filter(Boolean);
  const last = segments[segments.length - 1] ?? "";
  return releases.find((release) => release.slug === last) ?? null;
}

export default function UpdatesApp() {
  const release = findRelease();
  return release ? <ReleasePage release={release} /> : <UpdatesIndex />;
}
