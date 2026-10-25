# Computer Networks mode

CN mode turns supported experiment aims and a student's name, roll number and
optional section into a Word practical file. It does not need an inference API
key. The student selects Computer Networks, enters details, pastes aims (a
numbered index, one aim per line, or `---` separators), generates and downloads.
The Samples button loads all eight supported experiments.

The report has no cover page. It follows the supplied lab structure: Experiment
No., Aim, Description, Requirements, Network Topology, Procedure/Commands and
Result. It uses A4, Times New Roman, 16-point main headings, 14-point subheadings,
12-point text, numbered figures/tables and a course/page footer. Body prose uses
1.5 spacing; commands and tabular entries use single spacing for readability.

## Supported experiments

| Recipe | Coverage |
| --- | --- |
| Device basics | Packet Tracer, hubs/switches/routers and cable selection |
| Peer connectivity | Direct PC connection, static IP settings and ping checks |
| Star topology | Four PCs, a central switch, hub/repeater comparison instructions |
| Three switches | Six PCs, management SVIs, gateway router and STP verification |
| Subnetting | Class A/B/C examples, four FLSM subnets and VLSM for 100/50/20/10 hosts |
| Router basics | Interface/password commands, three-router static routes, two/three-router default variants |
| RIP | Three routers, RIPv2, no auto-summary, verification and link-failure exercise |
| OSPF | Three routers, area 0, explicit router IDs, verification and link-failure exercise |

Unsupported aims are rejected with a useful message rather than silently
producing an unrelated experiment. Separate RIP and OSPF aims. Arbitrary device
counts, custom subnets, BGP, ACLs, VLAN exercises and free-text CN refinement are
not supported in this release. Regenerate a recipe after changing the aim or
student details. CN classroom variations are hidden until individual student
identities can be supplied.

## Personalisation and consistency

`cn/experiments.py` is the source of truth. A stable SHA-256 mapping of the
student details selects a private address range. This is a deterministic
allocation, not a claim of uniqueness: different students can share a subnet.
Retries for the same student keep the same addresses. Device ports, host
settings, routes, tables, figures and verification commands are derived from
that one plan. Validation rejects duplicate IP addresses, network/broadcast
host addresses, reused link endpoints and incompatible directly connected IP
subnets. Subnet arithmetic uses Python's `ipaddress` module.

No model output is executed. Student details are plain text, limited to 100
characters per field. Preview image URLs accept PNG data URLs only. Word export
rebuilds the canonical experiment from the aim/profile instead of trusting
client-supplied figures or an asserted execution status. Large exports count the
actual number of CN figures against the shared image budget.

## Figures and execution status

The release generates topology and IP-setting figures, plus panels titled
Expected Connectivity Output or Expected Routing Output. They contain the
personalised settings and expected checks. They are **not screenshots from a
Packet Tracer run**. Generated records explicitly carry:

```json
{"execution":{"status":"not_run","engine":null}}
```

Ping statistics, latency and successful execution are not invented. Folder
sharing needs real computers or VMs; Packet Tracer PC-PT does not provide SMB
sharing. The report explains this distinction in the peer exercise.

## API and runner boundary

- `GET /api/cn/catalog` lists supported aims and reports `runner_connected:false`.
- `POST /api/generate` with `mode:"cn"`, `aim` and `student` returns the plan,
  report content and PNG previews without calling an LLM.
- `POST /api/cn/plan` accepts the same `aim` and `student` and returns the plan
  without image bytes. The UI also offers Download network plan.
- `POST /api/download` accepts `mode:"cn"` and experiment entries containing
  `aim`, `mode:"cn"` and `profile`. Auto mode can mix CN with OS/coding entries.

The JSON plan is an integration boundary, not an executable Packet Tracer
script. Some configuration entries describe alternative scenarios in plain
language. A future runner must select a scenario, resolve catalog model/port
names, apply actual CLI entries to the correct devices, wait for convergence,
verify both directions and attach captures to the exact plan and student.
No runner, screenshot-upload verification, remote job queue or `.pkt` generation
is implemented in this release. Hosting Packet Tracer execution requires a
separate service beyond the current Vercel function.

## Validation

```bash
pytest -q
node tests/test_cn_frontend.cjs
# Optional controller-to-server transport integration:
python app.py
PRACTIGEN_TEST_URL=http://127.0.0.1:5000 node tests/test_cn_frontend.cjs
```

Python coverage includes all recipes, address/CLI consistency, nonoverlapping
subnet calculations, rejected plans, image decoding, untrusted export inputs,
no-key generation, mixed-mode exports, pagination properties and export budgets.
The Node harness exercises the real frontend controller with a minimal DOM; it
is not a substitute for testing visual layout in a real browser. Document QA
should render a report containing all eight recipes and inspect the pages.
