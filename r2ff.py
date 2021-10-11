# Script that generates flathub style manifest
# TODO add logging
# TODO use click?
import yaml, json
import requests
import re
from pyrpm.spec import Spec, replace_macros
import sys
import subprocess

import logging

def get_input_args():
    #Get input args in the same format as "fedmod rpm2flatpak" command
    from_flathub = False
    flathub_id = None
    app_name = None
    force_rewrite = True

    for arg in sys.argv:
        if "--flathub" == arg[0:9]:
            from_flathub = True
            flathub_id = arg[10:]

    app_name = sys.argv[-1]

    force_rewrite = "--force" in sys.argv
    return from_flathub, flathub_id, app_name, force_rewrite 

def get_flathub_manifest(search_term):
    response = requests.get("https://flathub.org/api/v1/apps")
    response.raise_for_status()
    apps = response.json()

    matches = []
    search_lower = search_term.lower()
    for app in apps:
        if (search_lower in app['flatpakAppId'].lower() or
                search_lower in app['name'].lower()):
            matches.append((app['flatpakAppId'], app['name']))

    if len(matches) > 1:
        max_id_len = max([len(app_id) for app_id, _ in matches])
        for app_id, name in matches:
            print(app_id + (' ' * (max_id_len - len(app_id)) + ' ' + name))
        raise click.ClickException("Multiple matches found on flathub.org")
    elif len(matches) == 0:
        raise click.ClickException("No match found on flathub.org")

    app_id = matches[0][0]

    for fname, is_yaml in [
            (f"{app_id}.json", False),
            (f"{app_id}.yaml", True),
            (f"{app_id}.yml", -True)]:
        url = f"https://raw.githubusercontent.com/flathub/{app_id}/master/{fname}"
        response = requests.get(url)
        if response.status_code == 404:
            continue
        else:
            break

    response.raise_for_status()

    if is_yaml:
        return yaml.safe_load(response.text)
    else:
        # flatpak-builder supports non-standard comments in the manifest, strip
        # them out. (Ignore the possibility of C comments embedded in strings.)
        #
        # Regex explanation: matches /*<something>*/ (multiline)
        #    <something> DOES NOT contains "/*" substring
        no_comments = re.sub(r'/\*((?!/\*).)*?\*/', '', response.text, flags=re.DOTALL)
        return json.loads(no_comments)

def call_fedmod(flathub_id, from_flathub, force_rewrite, app_name):
    ret = 0
    params = ["fedmod", "rpm2flatpak"]
    params.append("--flathub="+flathub_id) if from_flathub else None
    params.append("--force") if force_rewrite else None
    params.append(app_name)
    ret = subprocess.call(params, stdout=subprocess.DEVNULL)
    return ret

def get_os_version():
    os_release = {}
    with open("/etc/os-release", "r") as f:
        for line in f.readlines():
            k, v = line.split("=")
            os_release[k] = v
    return os_release["VERSION_ID"]

def generate_manifest_generic():
    manifest = json.loads("{}")
    container = None
    with open("container.yaml", "r") as file:
        container = yaml.safe_load(file)
    manifest["app-id"] = container["flatpak"]["id"]
    manifest["runtime"] = "org.fedoraproject.Platform"
    os_version = get_os_version()
    manifest["runtime-version"] = "f"+os_version # todo tahat to z etc os release
    manifest["sdk"] = "org.fedoraproject.Sdk"
    manifest["command"] = container["flatpak"]["command"]
    manifest["finish-args"] = list(container["flatpak"]["finish-args"].split("\n"))
    manifest["modules"] = list()
    return manifest

def convert_deps(pkgs):
    url = "https://src.fedoraproject.org/rpms/{0}/raw/{1}/f/{0}.spec"
    sha_url = "https://src.fedoraproject.org/rpms/{0}/raw/{1}/f/sources"
    modules = []

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
        # TODO if using private repo, dont use the upstream
        try:
            dummy = pkgs[pkg_name]["repository"]
        except KeyError:
            pkg_url = url.format(pkg_name, pkgs[pkg_name]["ref"])
        else:    
            print("WARNING: Private repo in use for {}".format(pkg_name))
            pkg_url = None
        if not pkg_url:
            continue
        
        # get spec file
        r = requests.get(pkg_url)
        specfile_raw = r.content.decode(encoding=r.encoding)
        specfile = Spec.from_string(specfile_raw)
        
        ## TODO get buildsystem and extract parameters - currently not wokring properly
        ## pyrpm does not parse some things, so we have to do it manually for now:
        specfile_raw_lines = [line.rstrip() for line in specfile_raw.splitlines() if line != "\n"]
        for idx in range(len(specfile_raw_lines)):
            if specfile_raw_lines[idx] == "%build": # next line is build command
                buildsystem = specfile_raw_lines[idx+1].split(" ")[0]
                print("INFO buildsystem:", buildsystem, ", application: ", pkg_name)
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

        modules.append(module)
    return modules
        

            


if __name__ == "__main__":

    from_flathub, flathub_id, app_name, force_rewrite = get_input_args()
    # For grabbing finish args
    manifest = None
    if from_flathub:
        manifest = get_flathub_manifest(flathub_id)
    
    # Today, we cannot generate app.yaml on our own, but fedmod does
    # TODO Find out how fedmod generates app.yaml file for flatpaks and mimic its behaviour. 
    # Generate app.yaml and container.yaml
    ret = call_fedmod(flathub_id, from_flathub, force_rewrite, app_name)
    if ret:
        sys.exit(ret)
    
    # TODO allow downloading the files from src.fedoraproject.org/flatpaks if they exist
    
    # Generate a skelet of a flathub manifest
    manifest = generate_manifest_generic()

    

    # From this point: convert fedora-like package listing to flathub-like
    pkgs = []

    # get list of rpms used in fedora flatpak
    with open(app_name+".yaml", "r") as file:
        yaml_file = yaml.safe_load(file)
        pkgs = yaml_file["data"]["components"]["rpms"]

    modules = convert_deps(pkgs)
    manifest["modules"] = modules
    

    print(json.dumps(manifest, indent=4))