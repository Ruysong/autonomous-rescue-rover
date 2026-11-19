"""Local OpenAI-compatible llama.cpp endpoint. No model training or cloud API."""
import json
import urllib.request

SYSTEM = """You select exploration goals for a slow indoor rover searching for people.
Candidate bearing is relative to the rover: positive = LEFT, negative = RIGHT.
Image sides are only approximate hints for a forward-aligned camera.
Choose ONLY an ID from the supplied candidate snapshot. Prefer recent repeated person
observations when their direction is relevant, then unexplored boundaries, low weighted
travel cost and small turns. Boundary cells are NOT room area. Image-side observations
are NOT world coordinates or measured ranges. Do not invent people, distances or safety.
Return only JSON: {"candidate_id": integer or null, "reason": "short English explanation"}.
Use null to hold when evidence is insufficient. Never issue motor or safety commands. Use only a reason from the output schema;
never describe a person when recent_observations is empty."""

class LocalReasoner:
    def __init__(self, endpoint='http://127.0.0.1:8091', model='local-qwen3-vl-2b', timeout=15):
        self.endpoint, self.model, self.timeout = endpoint.rstrip('/'), model, timeout
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def __call__(self, snapshot):
        ids = [c['id'] for c in snapshot['candidates']]
        reasons = ['Low travel cost', 'Explore an unknown boundary']
        if snapshot.get('previous_choices') or any(c.get('failed_attempts') for c in snapshot['candidates']):
            reasons.append('Avoid previously attempted goals')
        if any(o.get('people') for o in snapshot.get('recent_observations', [])):
            reasons.append('Recheck recent visual observations')
        schema = {'type':'object' ,'properties':{
            'candidate_id':{'anyOf':[{'type':'integer','enum':ids},{'type':'null'}]},
            'reason':{'type':'string','enum':reasons}},'required':['candidate_id','reason'],
            'additionalProperties':False}
        payload = {'model':self.model, 'messages':[
            {'role':'system','content':SYSTEM},
            {'role':'user','content':json.dumps(snapshot,allow_nan=False)}],
            'temperature':0,'max_tokens':96,
            'response_format':{'type':'json_schema','json_schema':{'name':'goal', 'strict':True,'schema':schema}}}
        request = urllib.request.Request(self.endpoint+'/v1/chat/completions',
            json.dumps(payload).encode(), {'Content-Type':'application/json'})
        with self.http.open(request, timeout=self.timeout) as r:
            result=json.loads(r.read(65536))
        answer=json.loads(result['choices'][0]['message']['content'])
        if answer.get('reason') not in reasons:
            raise ValueError('Unsupported / ungrounded reason')
        return answer
