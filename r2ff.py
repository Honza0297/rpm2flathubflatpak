import yaml, json
import requests

from pyrpm.spec import Spec, replace_macros
import sys
import subprocess

# Get input args in the same format as "fedmod rpm2flatpak" command
from_flathub = False
flathub_id = None

for arg in sys.argv:
    if "--flathub" == arg[0:9]:
        from_flathub = True
        flathub_id = arg[10:]

app_name = sys.argv[-1]

no_generate_files = "--no-gen" in sys.argv

force_rewrite = "--force" in sys.argv

# Generate app.yaml and container.yaml
# TODO allow downloading the files from src.fedoraproject.org/flatpaks if they exist
if not no_generate_files: 
    params = ["fedmod", "rpm2flatpak"]
    params.append("--flathub="+flathub_id) if from_flathub else None
    params.append("--force") if force_rewrite else None
    params.append(app_name)
    ret = subprocess.call(params, stdout=subprocess.DEVNULL)
    if ret:
        exit(ret)
# Generate a skelet of a flathub manifest
manifest = json.loads("{}")
container = None
with open("container.yaml", "r") as file:
    container = yaml.safe_load(file)
manifest["app-id"] = container["flatpak"]["id"]
manifest["runtime"] = "org.fedoraproject.Platform"
manifest["runtime-version"] = "f34"
manifest["sdk"] = "org.fedoraproject.Sdk"
manifest["command"] = container["flatpak"]["command"]
manifest["finish-args"] = list(container["flatpak"]["finish-args"].split("\n"))
manifest["modules"] = list()

# From this point: convert fedora-like package listing to flathub-like
pkgs = []

# get list of rpms used in fedora flatpak
with open(app_name+".yaml", "r") as file:
    yaml_file = yaml.safe_load(file)
    pkgs = yaml_file["data"]["components"]["rpms"]


url = "https://src.fedoraproject.org/rpms/{0}/raw/{1}/f/{0}.spec"
sha_url = "https://src.fedoraproject.org/rpms/{0}/raw/{1}/f/sources"

# For all rpms:
for pkg_name in pkgs.keys():
    # TODO check if app is in flathub's shared modules. If so, use the configuration from there.

    # Init the module
    module = json.loads("{}")
    module["name"] = pkg_name
    module["buildsystem"] = None
    module["config-opts"] = list() # TODO
    module["sources"] = list()    

    # TODO there has to be a better way to check whether we use private repo...
    try:
        dummy = pkgs[pkg_name]["repository"]
    except KeyError:
        pkg_url = url.format(pkg_name, pkgs[pkg_name]["ref"])
    else:    
        print("NOTE: Private repo in use for {}".format(pkg_name))
        pkg_url = None
    if not pkg_url:
        continue
    
    # get spec file
    r = requests.get(pkg_url)
    specfile_raw = r.content.decode(encoding=r.encoding)
    specfile = Spec.from_string(specfile_raw)
    

    ## pyrpm does not parse some things, so we have to do it manually for now:
    specfile_raw_lines = [line.rstrip() for line in specfile_raw.splitlines() if line != "\n"]
    for idx in range(len(specfile_raw_lines)):
        if specfile_raw_lines[idx] == "%build": # next line is build command
            buildsystem = specfile_raw_lines[idx+1].split(" ")[0]
            if buildsystem[0] == "%":
                buildsystem = buildsystem[1:]

            #in case of something else than cmake, qmake, meson, autotools, cmake-ninja, use "simple" and pop config-opts
            supported_buildsystems = ["cmake", "qmake", "meson", "autotools", "cmake-ninja"]
            if buildsystem not in supported_buildsystems:
                print("Unsupported buildsystem:", buildsystem, ", application: ", pkg_name)
                module["buildsystem"] = "simple"
                module.pop("config-opts", None)
                module["build-commands"] = list() # TODO
            else:
                module["buildsystem"] = buildsystem
            

    # Get sources
    if hasattr(specfile, "sources_dict"):
        for spec_src in specfile.sources_dict:
            source = json.loads("{}")
            # Get source url and guess the type of source - archive, git, local file 
            src_url = replace_macros(specfile.sources_dict[spec_src], specfile)
            src_url_sliced = src_url.split("/")
            
            if not src_url_sliced[0].startswith("http"): # local file probably
                source["type"] = "file"
            elif "tar" in src_url_sliced[-1]:
                source["type"] = "archive"
            elif "git" in src_url_sliced[-1]: # probably a git repo
                source["type"] = "git"

            source["url"] = src_url

            # get spec file
            r = requests.get(sha_url.format(pkg_name, pkgs[pkg_name]["ref"]))
            sha_file_sliced = r.content.decode(encoding=r.encoding).split(" ")

            source[sha_file_sliced[0].lower()] = sha_file_sliced[-1].rstrip()   
            
            # append current source to sources of the current module
            module["sources"].append(source)

    # Get patches
    if hasattr(specfile, "patches_dict"):
        for spec_patch in specfile.patches_dict:
            patch = json.loads("{}")
            patch["type"] = "patch"
            patch["path"] = specfile.patches_dict[spec_patch]
            module["sources"].append(patch)

    manifest["modules"].append(module)
    

        


print(json.dumps(manifest, indent=4))