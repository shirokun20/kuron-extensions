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
```

## ☕ **Support Developer**

If you love **Kuron** and want to support its development, you can sponsor me on GitHub or buy me a coffee! ☕  

[![Sponsor shirokun20](https://img.shields.io/badge/Sponsor-shirokun20-ea4aaa?style=for-the-badge&logo=githubsponsors&logoColor=white)](https://github.com/sponsors/shirokun20)

You can also scan the QRIS below to donate:

<p align="center">
  <img src="assets/images/donation_qris.jpeg" width="300" alt="QRIS Donation" style="border-radius: 10px; box-shadow: 0 4px 8px rgba(0,0,0,0.1);"/>
</p>

> **Note:** Your support helps keep the servers running and the updates coming! 🚀

---
