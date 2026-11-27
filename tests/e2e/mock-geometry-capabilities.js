// Contrat applicatif synthétique : les tests de navigateur simulent les
// métadonnées, les tests Python vérifient leur extraction réelle du modèle.
function geometryCapabilities(overrides = {}) {
  return {
    schemaVersion: 1, resource: 'ouvrages-particuliers', target: 'da:concept:ouvrage-particulier',
    contractVersion: 'a'.repeat(64), modelDigest: 'b'.repeat(64), status: 'compatibility',
    allowedTypes: ['Point', 'LineString', 'Polygon'], required: true,
    create: true, edit: true, replace: true, convert: true, remove: false,
    validity: { preserveExisting: true, requiresConformingForValid: true,
      nonEmpty: true, nonDegenerate: true, spatiallyValid: true, dimensions: 2, srid: 'business' },
    diagnostics: [], ...overrides,
  };
}

async function mockGeometryCapabilities(route) {
  if (new URL(route.request().url()).pathname !== '/api/geometry-capabilities/ouvrages-particuliers') return false;
  await route.fulfill({ json: geometryCapabilities() });
  return true;
}

module.exports = { geometryCapabilities, mockGeometryCapabilities };
