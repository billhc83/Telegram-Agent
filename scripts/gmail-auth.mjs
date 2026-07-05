import { readFileSync, writeFileSync } from "fs";
import { createInterface } from "readline";
import { google } from "googleapis";

const CREDENTIALS_PATH = "/root/.gmail-mcp/gcp-oauth.keys.json";
const TOKEN_PATH = "/root/.gmail-mcp/token.json";
const SCOPES = [
  "https://www.googleapis.com/auth/gmail.modify",
  "https://www.googleapis.com/auth/gmail.settings.basic",
];

const creds = JSON.parse(readFileSync(CREDENTIALS_PATH, "utf8"));
const { client_id, client_secret } = creds.installed;

const oauth2Client = new google.auth.OAuth2(client_id, client_secret, "http://localhost");

const authUrl = oauth2Client.generateAuthUrl({
  access_type: "offline",
  scope: SCOPES,
});

console.log("\nOpen this URL in your browser:\n");
console.log(authUrl);
console.log(`
After clicking "Allow", your browser will be redirected to a URL that
starts with http://localhost/?code=...

The page will fail to load — that's fine. Copy the FULL URL from the
address bar and paste it here, then press Enter:
`);

const rl = createInterface({ input: process.stdin });
rl.question("Redirect URL: ", async (redirectUrl) => {
  rl.close();
  const url = new URL(redirectUrl.trim());
  const code = url.searchParams.get("code");
  if (!code) {
    console.error("Could not find 'code' in URL:", redirectUrl);
    process.exit(1);
  }
  const { tokens } = await oauth2Client.getToken(code);
  writeFileSync(TOKEN_PATH, JSON.stringify(tokens, null, 2));
  console.log("\ntoken.json saved. Gmail auth complete.");
});
