"""
pypcre 0.3.2 setup.py 패치 스크립트 (Dockerfile 빌드용)
원본 PyPI tar.gz의 setup() 호출에 name/version/packages 인자 추가
"""
import sys

path = '/tmp/pypcre_fix/pypcre-0.3.2/setup.py'
txt = open(path).read()

# 원본: name/version 없는 형태
old = 'setup(ext_modules=[EXTENSION], cmdclass={"build_ext": build_ext})'
new = 'setup(name="PyPcre", version="0.3.2", packages=["pcre"], package_dir={"pcre": "pcre"}, ext_modules=[EXTENSION], cmdclass={"build_ext": build_ext})'

if old in txt:
    open(path, 'w').write(txt.replace(old, new))
    print("PATCHED: name/version/packages added")
elif new in txt:
    print("ALREADY PATCHED")
else:
    print("ERROR: pattern not found. Current setup() call:")
    for line in txt.splitlines():
        if 'setup(' in line:
            print(' ', line)
    sys.exit(1)
