# rpm2flathubflatpak - r2ff
This tool is supposed to be used as a tool to create a flathub manifest from Fedora RPM sources (bypassing rpm packages). 
Currently, the manifest is only printed to stdout.


## Usage

$ python r2ff.py \<flags\> \<app-name\>

<Flags> are generally very similar to ones from "fedmod rpm2flatpak":

* --flathub=\<app-name\>
* --force (rewrite app.yaml and container.yaml if present)

Different from fedmod:
* --no-gen (do not generate yaml files, use existing ones instead)

Example:
$ python r2ff.py --flathub=org.gnome.Dictionary gnome-dictionary
  
Notes:
  
  * We cannot use --flatpak-common - there is no alternative in flathub world
