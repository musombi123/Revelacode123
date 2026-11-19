# T-Lang v1: compact executable procedure format

A .tlang file is UTF-8 JSON Lines: exactly one JSON object per non-empty line. The JSON envelope gives canonical parsing; the restricted steps make the language executable and auditable.

## Record shape

    {
      "version": "tlang/1",
      "id": "geometry.rectangle_area",
      "domain": "geometry",
      "task": "calculate rectangle area from length and width",
      "triggers": ["rectangle area", "area of rectangle"],
      "inputs": {"length": "number", "width": "number"},
      "steps": [
        {"op": "multiply", "args": [{"input": "length"}, {"input": "width"}], "as": "area"}
      ],
      "result": {"step": "area"},
      "tests": [
        {"input": {"length": 5, "width": 4}, "expect": 20},
        {"input": {"length": 2.5, "width": 8}, "expect": 20}
      ],
      "provenance": {"kind": "teacher", "teacher": "openai-compatible", "child_id": "child-a"}
    }

## Contract

- Inputs have explicit types: number, integer, string, boolean, or number_list.
- Each step is one allowlisted operation with literal arguments or references to declared inputs / prior steps.
- No loops, recursion, executable code, file/network access, dynamic evaluation, or model weights.
- At least two and at most twenty test vectors are mandatory. Home re-executes them.
- IDs are stable, lower-case identifiers. One record per child submission file.
- Provenance is metadata only. Raw model answers, prompts, user chats, personal information, and credentials must never be included.

## Verification boundary

The verifier can prove that the record conforms to the language and that its test cases pass under this interpreter. It cannot prove a general theorem merely because a teacher supplied two tests. For that reason, templates are suitable first for arithmetic, transformations, deterministic workflows, and well-specified domain rules. High-impact knowledge needs independent authoritative evidence and stronger validation.
