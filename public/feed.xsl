<?xml version="1.0" encoding="UTF-8"?>
<!--
  Human-friendly view for the Pancratius feeds. A browser that opens feed.xml
  renders this instead of raw XML: the channel, how to subscribe, and the items.
  Feed readers ignore the stylesheet and read the XML directly.
-->
<xsl:stylesheet version="1.0"
  xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
  xmlns:atom="http://www.w3.org/2005/Atom"
  xmlns:media="http://search.yahoo.com/mrss/">
  <xsl:output method="html" version="5.0" encoding="UTF-8" indent="yes"/>

  <xsl:template match="/">
    <xsl:variable name="ru" select="/rss/channel/language = 'ru'"/>
    <html lang="{/rss/channel/language}">
      <head>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        <title><xsl:value-of select="/rss/channel/title"/></title>
        <style>
          :root { color-scheme: dark; }
          * { box-sizing: border-box; }
          body {
            margin: 0;
            background: #100f0c;
            color: #e9e3d6;
            font-family: Georgia, "Times New Roman", serif;
            line-height: 1.6;
          }
          main { max-width: 42rem; margin: 0 auto; padding: 4rem 1.5rem 6rem; }
          .eyebrow {
            font-family: ui-sans-serif, system-ui, sans-serif;
            font-size: 11.5px; font-weight: 600; letter-spacing: 0.22em;
            text-transform: uppercase; color: #c79248; margin: 0 0 1rem;
          }
          h1 { font-size: 2.2rem; line-height: 1.15; margin: 0 0 0.6rem; font-weight: 500; }
          .desc { color: #b3ab9b; margin: 0 0 2rem; }
          .subscribe {
            border: 1px solid #2a2720; border-radius: 4px;
            padding: 1rem 1.2rem; margin: 0 0 2.5rem; background: #16140f;
          }
          .subscribe p { margin: 0 0 0.6rem; font-size: 0.95rem; color: #b3ab9b; }
          .subscribe code {
            display: block; font-family: ui-monospace, "SF Mono", Menlo, monospace;
            font-size: 0.9rem; color: #e9e3d6; word-break: break-all;
            background: #0c0b08; padding: 0.6rem 0.8rem; border-radius: 3px;
          }
          ul { list-style: none; margin: 0; padding: 0; }
          li { padding: 1.6rem 0; border-top: 1px solid #24211a; }
          li:first-child { border-top: 0; }
          .item-title {
            font-size: 1.3rem; color: #e9e3d6; text-decoration: none;
            display: inline-block; margin-bottom: 0.25rem;
          }
          .item-title:hover { color: #c79248; }
          .item-date {
            font-family: ui-sans-serif, system-ui, sans-serif;
            font-size: 11px; letter-spacing: 0.1em; text-transform: uppercase;
            color: #8c856f; margin: 0 0 0.5rem;
          }
          .item-desc { margin: 0; color: #b3ab9b; font-size: 0.98rem; }
        </style>
      </head>
      <body>
        <main>
          <p class="eyebrow">
            <xsl:choose>
              <xsl:when test="$ru">RSS-лента</xsl:when>
              <xsl:otherwise>Web feed</xsl:otherwise>
            </xsl:choose>
          </p>
          <h1><xsl:value-of select="/rss/channel/title"/></h1>
          <p class="desc"><xsl:value-of select="/rss/channel/description"/></p>

          <div class="subscribe">
            <p>
              <xsl:choose>
                <xsl:when test="$ru">Это лента обновлений. Вставьте этот адрес в любую программу для чтения RSS, чтобы подписаться:</xsl:when>
                <xsl:otherwise>This is a feed of updates. Paste this address into any RSS reader to subscribe:</xsl:otherwise>
              </xsl:choose>
            </p>
            <code><xsl:value-of select="/rss/channel/atom:link[@rel='self']/@href"/></code>
          </div>

          <ul>
            <xsl:for-each select="/rss/channel/item">
              <li>
                <a class="item-title" href="{link}"><xsl:value-of select="title"/></a>
                <p class="item-date"><xsl:value-of select="substring(pubDate, 6, 11)"/></p>
                <p class="item-desc"><xsl:value-of select="description"/></p>
              </li>
            </xsl:for-each>
          </ul>
        </main>
      </body>
    </html>
  </xsl:template>
</xsl:stylesheet>
