# Kuron Extensions 🧩

![GitHub repo size](https://img.shields.io/github/repo-size/shirokun20/kuron-extensions)
![License](https://img.shields.io/github/license/shirokun20/kuron-extensions)

Official repository for content source extensions and JSON configurations for the **Kuron** reader app. 

These extensions allow Kuron to dynamically parse and scrape content from various websites using our Generic Scraper Engine, without needing to recompile the main app.

## 📖 How to Use

1. Open the **Kuron App**.
2. Go to **Settings > Sources > Extension Repository**.
3. Add a new repository and paste the following URL:
   ```text
   https://raw.githubusercontent.com/shirokun20/kuron-extensions/main/manifest.json
   ```
4. You will see the list of all available extensions and can install them directly from the app.

## 🛠️ Building an Extension

Kuron uses a JSON-based configuration system (and sometimes XHR proxies) to build sources. You don't need to write Dart code to create an extension!

See the [Documentation/Wiki](link-ke-wiki) for the full JSON schema and selectors guide.

### Basic Structure
```json
{
  "sourceId": "example-source",
  "name": "Example Manga",
  "baseUrl": "https://example.com",
  "type": "generic_scraper",
  "selectors": { ... }
}
