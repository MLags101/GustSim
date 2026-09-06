import argparse
import json
from pathlib import Path
from . import db, geometry, foam, validation
from .models import SimulationSpec

def main():
    parser=argparse.ArgumentParser(description='GustSim reproducible CFD tools')
    sub=parser.add_subparsers(dest='command',required=True)
    imp=sub.add_parser('import');imp.add_argument('file',type=Path);imp.add_argument('--units',default='mm',choices=geometry.UNITS)
    default=sub.add_parser('defaults');default.add_argument('geometry_id');default.add_argument('output',type=Path)
    compile=sub.add_parser('compile');compile.add_argument('spec',type=Path);compile.add_argument('output',type=Path)
    validate=sub.add_parser('validate');validate.add_argument('spec',type=Path)
    run=sub.add_parser('run');run.add_argument('spec',type=Path);run.add_argument('--mesh-only',action='store_true')
    sub.add_parser('worker')
    args=parser.parse_args();db.initialize()
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
