// Minimal Streamlit components v1 bridge for the session cookie.
// Protocol (Streamlit >= 1.x): the iframe must announce "streamlit:componentReady"
// with apiVersion 1 before Streamlit sends "streamlit:render" messages; values are
// returned with "streamlit:setComponentValue" ({value, dataType}) and the frame
// height with "streamlit:setFrameHeight" ({height}).
const cookieName = "invoiceops_session";
const send = (type, data) => window.parent.postMessage({isStreamlitMessage: true, type, ...data}, "*");
const secureFlag = () => (window.location.protocol === "https:" ? "; Secure" : "");

window.addEventListener("message", (event) => {
  const message = event.data;
  if (!message || message.type !== "streamlit:render") return;

  const args = message.args || {};
  let value = null;
  if (args.action === "get") {
    const entry = document.cookie.split("; ").find((item) => item.startsWith(cookieName + "="));
    value = entry ? decodeURIComponent(entry.slice(cookieName.length + 1)) : "";
  } else if (args.action === "set") {
    document.cookie = `${cookieName}=${encodeURIComponent(args.value)}; Max-Age=43200; Path=/; SameSite=Lax${secureFlag()}`;
    value = "saved";
  } else if (args.action === "remove") {
    document.cookie = `${cookieName}=; Max-Age=0; Path=/; SameSite=Lax${secureFlag()}`;
    value = "removed";
  }
  send("streamlit:setComponentValue", {value, dataType: "json"});
});

send("streamlit:componentReady", {apiVersion: 1});
send("streamlit:setFrameHeight", {height: 0});
