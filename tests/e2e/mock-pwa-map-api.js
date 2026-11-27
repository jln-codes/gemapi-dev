const { mockBusinessCRS } = require("./mock-business-crs");

async function mockMapApi(context) {
  await context.route("**/api/**", async (route) => {
    if (await mockBusinessCRS(route)) return;
    const path = new URL(route.request().url()).pathname;
    const empty = { type: "FeatureCollection", features: [] };
    const json = path === "/api/config"
      ? { legacy_single_database_enabled: true, show_uuid: false }
      : path === "/api/resource-events/head" ? { cursor: "0" }
      : path === "/api/resource-events" ? { events: [], hasMore: false, nextCursor: "0" }
      : path === "/api/base-summary" ? { crs: { authority: "EPSG", srid: 3950,
        code: "EPSG:3950", name: "RGF93 / CC50" } }
      : ["/api/troncons", "/api/desordres", "/api/territoire-administratif"]
        .includes(path) ? empty
      : path === "/api/troncons/options" ? { troncons: [] }
      : path.startsWith("/api/referentiels/") ? { types: [], categories: [], references: [] }
      : path === "/api/systemes-endiguement" ? { systemes: [] } : {};
    await route.fulfill({ json });
  });
}

module.exports = { mockMapApi };
