"""Portable data-volume backup/restore. Stop api and worker before use."""
import argparse
from pathlib import Path
import tarfile
from . import config

def create(target):
    target=Path(target).resolve()
    if target.is_relative_to(config.DATA):raise ValueError('Write the backup outside the data volume')
    with tarfile.open(target,'w:gz') as archive:
        for child in config.DATA.iterdir():
            if child.name not in {'worker.lock','exports'}:
                archive.add(child,arcname=child.name,recursive=True)
    return target

def restore(source):
    config.DATA.mkdir(parents=True,exist_ok=True)
    if any(config.DATA.iterdir()):raise ValueError('Restore requires an empty data volume; existing data is never overwritten')
    with tarfile.open(source,'r:gz') as archive:
        for member in archive.getmembers():
            if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):raise ValueError('Unsupported archive entry')
            if not (config.DATA/member.name).resolve().is_relative_to(config.DATA):raise ValueError('Unsafe archive path')
        archive.extractall(config.DATA,filter='data')

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['create','restore']);p.add_argument('archive',type=Path);args=p.parse_args()
    if args.action=='create':print(create(args.archive))
    else:restore(args.archive);print('Data restored')

if __name__=='__main__':main()
