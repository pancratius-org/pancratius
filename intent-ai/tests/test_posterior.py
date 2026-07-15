# research-pure: the serialized posterior reproduces the fitted student, with no sklearn on the path.
"""`export_posterior` + `StandardizedLinearPosterior` are the model-as-data boundary: a consumer
applies them with stdlib math and gets the SAME score sklearn would. If this drifts, the importer
and the research student silently disagree, so the equivalence is proven line by line."""
from __future__ import annotations

import json

import pytest
from intent_ai import student
from intent_ai.posterior import StandardizedLinearPosterior, export_posterior


@pytest.fixture(scope="module")
def fitted(corpus):
    records, labelset = corpus
    ds = student.build_dataset(records, labelset)
    model = student.fit_full(ds)
    feats = [r.features for recs in records.values() for r in recs if r.votable]
    return model, feats


@pytest.mark.corpus_cache
def test_sklearn_free_posterior_matches_fitted(fitted):
    model, feats = fitted
    post = export_posterior(model)
    sample = feats[:500]
    assert post.posteriors(sample) == pytest.approx(model.posteriors(sample), abs=1e-9)


@pytest.mark.corpus_cache
def test_json_round_trip_is_lossless(fitted):
    model, feats = fitted
    post = export_posterior(model)
    restored = StandardizedLinearPosterior.from_dict(json.loads(json.dumps(post.to_dict())))
    assert restored == post
    assert restored.posteriors(feats[:200]) == pytest.approx(
        post.posteriors(feats[:200]), abs=1e-12,
    )


def test_from_dict_rejects_foreign_schema():
    with pytest.raises(ValueError, match="schema"):
        StandardizedLinearPosterior.from_dict({"schema": "something.else", "features": []})


@pytest.mark.corpus_cache
def test_from_dict_rejects_stale_feature_contract(fitted):
    model, _ = fitted
    payload = export_posterior(model).to_dict()
    payload["feature_schema_version"] = "features-2"

    with pytest.raises(ValueError, match="feature contract"):
        StandardizedLinearPosterior.from_dict(payload)
