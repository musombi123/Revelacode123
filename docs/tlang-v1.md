# T-Lang v1 — Executable Template Format

T-Lang v1 is a constrained JSON domain-specific language, not natural-language notes and not a model-weight format.

## Template fields

A template contains id, version, domain, intent, optional triggers, typed inputs, ordered steps, output, at least two input/expected test vectors, provenance, and an optional signed submission envelope.

## References

Use {"$ref":"input.name"} for an input or {"$ref":"step.result"} for the output of an earlier step. Forward references, cycles, arbitrary expressions, Python, shell commands, imports, and dynamic code are unsupported.

## Version 1 operators

Text: text.trim, text.lower, text.upper, text.word_count, text.contains, text.slug, text.split, text.join.

Math: math.add, math.subtract, math.multiply, math.divide, math.mean, math.min, math.max, math.round, math.clamp, math.modulo, math.sqrt.

Lists: list.unique, list.sort_numbers.

The operator registry is intentionally narrow. Extending it requires implementation, tests, and a versioned specification change.

## Verification

The verifier checks structure, the operation allowlist, references, finite input/output tests, duplicate fingerprints, detected email and credential patterns, and forbidden raw-output fields. Template size is limited to 32 KiB and operation count to 64.

Passing establishes reproducible behavior for declared test vectors, not proof for every possible input or the truth of an external factual claim.
