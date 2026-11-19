"""Model-shaped client adapter for the RevelaCode Legend child HTTP API."""
from __future__ import annotations
import os
import requests

class LegendForReasoning:
    def __init__(self, endpoint=None, api_key=None, timeout=30):
        self.endpoint=(endpoint or os.environ.get("LEGEND_API_URL","")).rstrip("/")
        if not self.endpoint:
            raise ValueError("Set LEGEND_API_URL to the deployed Legend child endpoint")
        self.api_key=api_key or os.environ.get("LEGEND_CHILD_API_KEY")
        self.timeout=timeout

    def __call__(self, inputs):
        headers={"Content-Type":"application/json"}
        if self.api_key:
            headers["x-child-id"]=os.environ.get("LEGEND_CHILD_ID","")
            headers["x-child-key"]=self.api_key
        response=requests.post(self.endpoint+"/generate",json={"inputs":inputs},headers=headers,timeout=self.timeout)
        response.raise_for_status()
        return response.json()

if __name__=="__main__":
    import argparse, json
    parser=argparse.ArgumentParser(description="Run a request against a Legend child API")
    parser.add_argument("inputs",help="Task text or JSON task object")
    args=parser.parse_args()
    try:
        parsed=json.loads(args.inputs)
    except json.JSONDecodeError:
        parsed=args.inputs
    print(json.dumps(LegendForReasoning()(parsed),indent=2))
