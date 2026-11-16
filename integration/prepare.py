# SPDX-License-Identifier: LGPL-3.0-or-later
"""Compatibility wrapper around the public detector layout generator."""
import argparse
from .layout import default_layout, generate

def prepare(root, case):
    return generate(default_layout(), root, analysis=True, mode=case)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory', required=True)
    parser.add_argument('--mode', choices=('two','off','manual','replay'), default='two')
    args=parser.parse_args()
    print(prepare(args.directory,args.mode))

if __name__=='__main__': main()
