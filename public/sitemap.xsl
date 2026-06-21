<?xml version="1.0" encoding="UTF-8"?>
<!--
  Human-friendly view for the Pancratius sitemaps. A browser that opens
  sitemap-*.xml renders this instead of raw XML; crawlers ignore the stylesheet
  and read the XML directly.
-->
<xsl:stylesheet version="1.0"
  xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
  xmlns:s="http://www.sitemaps.org/schemas/sitemap/0.9"
  xmlns:xhtml="http://www.w3.org/1999/xhtml">
  <xsl:output method="html" version="5.0" encoding="UTF-8" indent="yes"/>

  <xsl:template match="/">
    <html lang="en">
      <head>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        <meta name="robots" content="noindex"/>
        <title>Pancratius — XML Sitemap</title>
        <style>
          :root { color-scheme: dark; }
          * { box-sizing: border-box; }
          body {
            margin: 0;
            background: #100f0c;
            color: #e9e3d6;
            font-family: Georgia, "Times New Roman", serif;
            line-height: 1.55;
          }
          main { max-width: 60rem; margin: 0 auto; padding: 4rem 1.5rem 6rem; }
          .eyebrow {
            font-family: ui-sans-serif, system-ui, sans-serif;
            font-size: 11.5px; font-weight: 600; letter-spacing: 0.22em;
            text-transform: uppercase; color: #c79248; margin: 0 0 1rem;
          }
          h1 { font-size: 2.2rem; line-height: 1.15; margin: 0 0 0.6rem; font-weight: 500; }
          .desc { color: #b3ab9b; margin: 0 0 2.5rem; max-width: 42rem; }
          table { width: 100%; border-collapse: collapse; }
          th {
            text-align: left; padding: 0 0 0.7rem;
            font-family: ui-sans-serif, system-ui, sans-serif;
            font-size: 11px; font-weight: 600; letter-spacing: 0.14em;
            text-transform: uppercase; color: #8c856f;
            border-bottom: 1px solid #2a2720;
          }
          td { padding: 0.55rem 0; border-bottom: 1px solid #1c1a15; vertical-align: baseline; }
          td.url a {
            color: #e9e3d6; text-decoration: none;
            font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 0.82rem;
            word-break: break-all;
          }
          td.url a:hover { color: #c79248; text-decoration: underline; }
          td.langs { width: 7rem; white-space: nowrap; }
          .lang {
            display: inline-block; margin-right: 0.3rem;
            font-family: ui-sans-serif, system-ui, sans-serif;
            font-size: 10px; font-weight: 600; letter-spacing: 0.08em;
            text-transform: uppercase; color: #8c856f;
            border: 1px solid #2a2720; border-radius: 3px; padding: 0.05rem 0.35rem;
          }
        </style>
      </head>
      <body>
        <main>
          <p class="eyebrow">XML Sitemap</p>
          <h1>Pancratius</h1>
          <p class="desc">
            <xsl:value-of select="count(s:urlset/s:url)"/> pages, listed for search engines.
            Each row links to the page and shows the languages it is available in.
          </p>
          <table>
            <thead>
              <tr><th>URL</th><th>Languages</th></tr>
            </thead>
            <tbody>
              <xsl:for-each select="s:urlset/s:url">
                <tr>
                  <td class="url"><a href="{s:loc}"><xsl:value-of select="s:loc"/></a></td>
                  <td class="langs">
                    <xsl:for-each select="xhtml:link[@hreflang != 'x-default']">
                      <span class="lang"><xsl:value-of select="@hreflang"/></span>
                    </xsl:for-each>
                  </td>
                </tr>
              </xsl:for-each>
            </tbody>
          </table>
        </main>
      </body>
    </html>
  </xsl:template>
</xsl:stylesheet>
