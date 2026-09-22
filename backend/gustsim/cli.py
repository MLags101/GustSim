import argparse
import json
import os
from pathlib import Path
from . import db, geometry, foam, validation
from .models import SimulationSpec

def _url(parser):
    parser.add_argument('--url', default=os.environ.get('GUSTSIM_URL', 'http://127.0.0.1:8080'), help='GustSim API, usually http://127.0.0.1:8080')

def main():
    parser=argparse.ArgumentParser(description='GustSim reproducible CFD tools')
    sub=parser.add_subparsers(dest='command',required=True)
    imp=sub.add_parser('import');imp.add_argument('file',type=Path);imp.add_argument('--units',default='mm',choices=geometry.UNITS)
    default=sub.add_parser('defaults');default.add_argument('geometry_id');default.add_argument('output',type=Path)
    compile=sub.add_parser('compile');compile.add_argument('spec',type=Path);compile.add_argument('output',type=Path)
    validate=sub.add_parser('validate');validate.add_argument('spec',type=Path)
    run=sub.add_parser('run');run.add_argument('spec',type=Path);run.add_argument('--mesh-only',action='store_true')
    sub.add_parser('worker')
    agent=sub.add_parser('agent', help='Call the coding-agent tools against a running GustSim')
    agent_commands=agent.add_subparsers(dest='agent_command', required=True)
    _url(agent_commands.add_parser('tools', help='Print the tool schemas as JSON'))
    call=agent_commands.add_parser('call', help='Call one tool. Pass a JSON object, or - to read it from stdin.')
    _url(call); call.add_argument('name'); call.add_argument('arguments', nargs='?', default='{}')
    mcp=sub.add_parser('mcp', help='Serve the same tools over MCP on stdin/stdout')
    _url(mcp)
    args=parser.parse_args()
    if args.command=='mcp':
        from .agent import serve_stdio
        serve_stdio(args.url)
        return
    if args.command=='agent':
        from .agent import run_cli
        run_cli(args)
        return
    db.initialize()
    if args.command=='import':
        data=args.file.read_bytes()
        result=geometry.import_stl(args.file.name,data,args.units) if args.file.suffix.lower()=='.stl' else geometry.import_step(args.file.name,data)
        print(result['id'])
    elif args.command=='defaults':args.output.write_text(validation.defaults(args.geometry_id).model_dump_json(indent=2),encoding='utf-8')
    elif args.command=='worker':
        from .worker import main as worker_main
        worker_main()
    else:
        spec=SimulationSpec.model_validate_json(args.spec.read_text(encoding='utf-8'))
        if args.command=='compile':print(foam.compile_case(spec,args.output))
        elif args.command=='validate':
            report=validation.validate(spec);print(json.dumps(report,indent=2));raise SystemExit(0 if report['valid'] else 1)
        else:
            report=validation.validate(spec)
            if not report['valid']:raise SystemExit('; '.join(report['errors']))
            print(db.enqueue(spec.model_dump(),'mesh' if args.mesh_only else 'solve')['id'])

if __name__=='__main__':main()
