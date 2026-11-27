const parameters = Object.freeze({
  economique: Object.freeze({ max_long_edge_px: 1024, output_mime_type: 'image/jpeg', encoder_quality: 0.68,
    min_long_edge_px: 640, min_encoder_quality: 0.46, encoder_quality_step: 0.08, dimension_scale_step: 0.8 }),
  standard: Object.freeze({ max_long_edge_px: 1600, output_mime_type: 'image/jpeg', encoder_quality: 0.78,
    min_long_edge_px: 960, min_encoder_quality: 0.46, encoder_quality_step: 0.08, dimension_scale_step: 0.8 }),
  haute_qualite: Object.freeze({ max_long_edge_px: 2400, output_mime_type: 'image/jpeg', encoder_quality: 0.86,
    min_long_edge_px: 1620, min_encoder_quality: 0.46, encoder_quality_step: 0.08, dimension_scale_step: 0.8 }),
});

function photoQualityPolicy(profile = 'standard', policyVersion = 1, maxFileSizeBytes = 1024 * 1024) {
  return {
    contract_version: 2,
    parameters_version: 2,
    profile,
    policy_version: policyVersion,
    max_file_size_bytes: maxFileSizeBytes,
    effective_parameters: { ...parameters[profile], max_file_size_bytes: maxFileSizeBytes },
  };
}

module.exports = { parameters, photoQualityPolicy };
