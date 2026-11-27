//! Dev boxes (distrobox) and containers (podman, Quadlet): what the tools print,
//! the command behind each button, and the Quadlet file Machines writes. std +
//! serde_json only.
//!
//! One podman list feeds both pages: a container labelled `manager=distrobox`
//! is a dev box; one labelled `PODMAN_SYSTEMD_UNIT` belongs to a Quadlet
//! service (shown with its service, not as a loose container); the rest are
//! plain containers.

use serde_json::Value;

fn s(v: &Value, k: &str) -> String {
    v.get(k).and_then(Value::as_str).unwrap_or_default().to_string()
}

fn argv(parts: &[&str]) -> Vec<String> {
    parts.iter().map(|p| p.to_string()).collect()
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Container {
    pub id: String,
    pub name: String,
    pub image: String,
    /// running, exited, created, paused...
    pub state: String,
    /// podman's own words: "Up 3 hours", "Exited (0) 2 days ago"
    pub status: String,
    pub ports: Vec<String>,
    pub distrobox: bool,
    /// The Quadlet service that runs it, e.g. "jellyfin.service".
    pub unit: Option<String>,
}

impl Container {
    pub fn running(&self) -> bool {
        self.state == "running"
    }
}

fn ports(v: &Value) -> Vec<String> {
    v.get("Ports")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .map(|p| {
                    let host = p.get("host_port").and_then(Value::as_u64).unwrap_or(0);
                    let cont = p.get("container_port").and_then(Value::as_u64).unwrap_or(0);
                    let proto = p.get("protocol").and_then(Value::as_str).unwrap_or("tcp");
                    let ip = p.get("host_ip").and_then(Value::as_str).unwrap_or("");
                    let ip = if ip.is_empty() { String::new() } else { format!("{ip}:") };
                    let suffix = if proto == "tcp" { String::new() } else { format!("/{proto}") };
                    format!("{ip}{host}→{cont}{suffix}")
                })
                .collect()
        })
        .unwrap_or_default()
}

/// `podman ps -a --format json`.
pub fn parse_ps(text: &str) -> Result<Vec<Container>, String> {
    let v: Value = serde_json::from_str(text).map_err(|e| format!("podman ps: {e}"))?;
    let mut out: Vec<Container> = v
        .as_array()
        .map(|a| {
            a.iter()
                .map(|c| {
                    let labels = c.get("Labels");
                    let label = |k: &str| labels.and_then(|l| l.get(k)).and_then(Value::as_str).map(String::from);
                    Container {
                        id: s(c, "Id"),
                        name: c
                            .get("Names")
                            .and_then(Value::as_array)
                            .and_then(|n| n.first())
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_string(),
                        image: s(c, "Image"),
                        state: s(c, "State"),
                        status: s(c, "Status"),
                        ports: ports(c),
                        distrobox: label("manager").as_deref() == Some("distrobox"),
                        unit: label("PODMAN_SYSTEMD_UNIT"),
                    }
                })
                .collect()
        })
        .unwrap_or_default();
    out.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));
    Ok(out)
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Image {
    pub id: String,
    pub name: String,
    pub size: u64,
    pub containers: u64,
}

/// `podman images --format json`.
pub fn parse_images(text: &str) -> Result<Vec<Image>, String> {
    let v: Value = serde_json::from_str(text).map_err(|e| format!("podman images: {e}"))?;
    let mut out: Vec<Image> = v
        .as_array()
        .map(|a| {
            a.iter()
                .map(|i| Image {
                    id: s(i, "Id"),
                    name: i
                        .get("Names")
                        .and_then(Value::as_array)
                        .and_then(|n| n.first())
                        .and_then(Value::as_str)
                        .map(String::from)
                        .unwrap_or_else(|| format!("<none> {}", s(i, "Id").chars().take(12).collect::<String>())),
                    size: i.get("Size").and_then(Value::as_u64).unwrap_or(0),
                    containers: i.get("Containers").and_then(Value::as_u64).unwrap_or(0),
                })
                .collect()
        })
        .unwrap_or_default();
    out.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(out)
}

pub fn human(bytes: u64) -> String {
    let b = bytes as f64;
    if b >= 1e9 {
        format!("{:.1} GB", b / 1e9)
    } else if b >= 1e6 {
        format!("{:.0} MB", b / 1e6)
    } else {
        format!("{:.0} kB", b / 1e3)
    }
}

// --------------------------------------------------------------- dev boxes

/// The distributions New box offers: (shown name, image).
pub const DISTROS: &[(&str, &str)] = &[
    ("Ubuntu 24.04 LTS", "docker.io/library/ubuntu:24.04"),
    ("Fedora 43", "registry.fedoraproject.org/fedora-toolbox:43"),
    ("Debian 13", "docker.io/library/debian:13"),
    ("Arch Linux", "docker.io/library/archlinux:latest"),
];

pub fn valid_name(n: &str) -> bool {
    let b = n.as_bytes();
    !b.is_empty()
        && b.len() <= 63
        && b[0].is_ascii_alphanumeric()
        && b.iter().all(|c| c.is_ascii_alphanumeric() || b"_.-".contains(c))
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct NewBox {
    pub name: String,
    /// Index into DISTROS, or DISTROS.len() for "another image".
    pub distro: usize,
    pub image: String,
    pub nvidia: bool,
    /// Its own home folder (~/.local/share/boxes/NAME) instead of yours.
    pub own_home: bool,
}

impl NewBox {
    pub fn image(&self) -> String {
        DISTROS.get(self.distro).map(|d| d.1.to_string()).unwrap_or_else(|| self.image.trim().to_string())
    }

    pub fn problem(&self, taken: &[String]) -> Option<String> {
        let n = self.name.trim();
        if !valid_name(n) {
            return Some("A name: letters, digits, dots, dashes and underscores (like dev or ubuntu).".into());
        }
        if taken.iter().any(|t| t == n) {
            return Some(format!("There is already a container called {n}."));
        }
        if self.image().is_empty() {
            return Some("Which image? e.g. docker.io/library/alpine:latest".into());
        }
        None
    }

    pub fn argv(&self, home: &str) -> Vec<String> {
        let n = self.name.trim();
        let mut a = argv(&["distrobox", "create", "--yes", "--name", n, "--image"]);
        a.push(self.image());
        if self.nvidia {
            a.push("--nvidia".into());
        }
        if self.own_home {
            a.extend(["--home".into(), format!("{home}/.local/share/boxes/{n}")]);
        }
        a
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum BoxAction {
    Stop,
    Delete,
}

impl BoxAction {
    pub fn argv(&self, name: &str) -> Vec<String> {
        match self {
            BoxAction::Stop => argv(&["distrobox", "stop", "--yes", name]),
            BoxAction::Delete => argv(&["distrobox", "rm", "--force", name]),
        }
    }
}

pub fn box_enter(name: &str) -> Vec<String> {
    argv(&["distrobox", "enter", name])
}

/// Upgrading runs the box's package manager: shown in a terminal, it can take a while.
pub fn box_upgrade(name: &str) -> Vec<String> {
    argv(&["distrobox", "upgrade", name])
}

/// The apps a box has, as desktop-file names (runs in the box, starting it).
pub fn box_apps(name: &str) -> Vec<String> {
    argv(&["distrobox", "enter", name, "--", "sh", "-c", "ls /usr/share/applications/ 2>/dev/null"])
}

pub fn parse_box_apps(text: &str) -> Vec<String> {
    let mut v: Vec<String> = text
        .lines()
        .map(str::trim)
        .filter(|l| l.ends_with(".desktop"))
        .map(|l| l.trim_end_matches(".desktop").to_string())
        .collect();
    v.sort();
    v
}

pub fn box_export(name: &str, app: &str) -> Vec<String> {
    argv(&["distrobox", "enter", name, "--", "distrobox-export", "--app", app])
}

// -------------------------------------------------------------- containers

#[derive(Debug, Clone, PartialEq)]
pub enum ContainerAction {
    Start,
    Stop,
    Restart,
    Remove,
}

impl ContainerAction {
    pub fn argv(&self, name: &str) -> Vec<String> {
        match self {
            ContainerAction::Start => argv(&["podman", "start", name]),
            ContainerAction::Stop => argv(&["podman", "stop", name]),
            ContainerAction::Restart => argv(&["podman", "restart", name]),
            ContainerAction::Remove => argv(&["podman", "rm", "--force", name]),
        }
    }
}

pub fn container_logs(name: &str) -> Vec<String> {
    argv(&["podman", "logs", "--tail", "300", name])
}

pub fn container_shell(name: &str) -> Vec<String> {
    argv(&["podman", "exec", "-it", name, "sh", "-c", "command -v bash >/dev/null && exec bash || exec sh"])
}

// --------------------------------------------------------- Quadlet services

/// A `.container` file in ~/.config/containers/systemd.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Service {
    pub name: String,
    pub path: String,
    pub image: String,
    pub ports: Vec<String>,
    /// [Install] WantedBy=... : started at boot (with linger) or login.
    pub autostart: bool,
    pub system: bool,
    /// From systemctl: active, inactive, failed, activating...
    pub state: String,
    /// Written by Machines (first line), so it may rewrite it.
    pub ours: bool,
}

/// The parts of a `.container` file Machines shows. Unknown keys are left alone.
pub fn parse_quadlet(name: &str, path: &str, text: &str, system: bool) -> Service {
    let mut s = Service { name: name.into(), path: path.into(), system, ..Default::default() };
    s.ours = text.starts_with(MARK);
    let mut section = String::new();
    for line in text.lines() {
        let l = line.trim();
        if l.starts_with('[') && l.ends_with(']') {
            section = l[1..l.len() - 1].to_string();
            continue;
        }
        let Some((k, v)) = l.split_once('=') else { continue };
        let (k, v) = (k.trim(), v.trim());
        match (section.as_str(), k) {
            ("Container", "Image") => s.image = v.into(),
            ("Container", "PublishPort") => s.ports.push(v.into()),
            ("Install", "WantedBy") => s.autostart = true,
            _ => {}
        }
    }
    s
}

pub const MARK: &str = "# Made by Machines (pengyos-machines).";

/// What Run a container (as a service) and Keep running at boot write.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct QuadletSpec {
    pub name: String,
    pub image: String,
    /// PublishPort values: [ip:]host:container[/proto]
    pub ports: Vec<String>,
    /// Volume values: host-or-volume:container[:options]
    pub volumes: Vec<String>,
    pub env: Vec<(String, String)>,
    pub gpu: bool,
}

impl QuadletSpec {
    pub fn file_name(&self) -> String {
        format!("{}.container", self.name)
    }

    pub fn unit(&self) -> String {
        format!("{}.service", self.name)
    }

    pub fn render(&self) -> String {
        let mut t = format!(
            "{MARK}\n# Edit freely; `systemctl --user daemon-reload` after a change.\n[Unit]\nDescription={} (container)\n\n[Container]\nImage={}\nContainerName={}\n",
            self.name, self.image, self.name
        );
        for p in &self.ports {
            t += &format!("PublishPort={p}\n");
        }
        for v in &self.volumes {
            t += &format!("Volume={v}\n");
        }
        for (k, v) in &self.env {
            // Quadlet splits Environment= like systemd: quote a value with spaces.
            if v.contains(char::is_whitespace) || v.contains('"') {
                t += &format!("Environment=\"{}={}\"\n", k, v.replace('\\', "\\\\").replace('"', "\\\""));
            } else {
                t += &format!("Environment={k}={v}\n");
            }
        }
        if self.gpu {
            t += "AddDevice=nvidia.com/gpu=all\n";
        }
        t += "\n[Service]\nRestart=on-failure\n\n[Install]\nWantedBy=default.target\n";
        t
    }

    /// From `podman container inspect NAME` (one object or a list of one) and
    /// the image's own environment (`podman image inspect`), which is left out.
    pub fn from_inspect(inspect: &str, image_env: &[String]) -> Result<QuadletSpec, String> {
        let v: Value = serde_json::from_str(inspect).map_err(|e| format!("podman inspect: {e}"))?;
        let c = v.as_array().and_then(|a| a.first()).unwrap_or(&v);
        let name = c.get("Name").and_then(Value::as_str).unwrap_or_default().trim_start_matches('/').to_string();
        let image = c
            .get("ImageName")
            .and_then(Value::as_str)
            .or_else(|| c.get("Config").and_then(|x| x.get("Image")).and_then(Value::as_str))
            .unwrap_or_default()
            .to_string();
        if name.is_empty() || image.is_empty() {
            return Err("podman inspect did not say the container's name and image".into());
        }
        let mut ports = Vec::new();
        if let Some(b) = c.get("HostConfig").and_then(|h| h.get("PortBindings")).and_then(Value::as_object) {
            let mut keys: Vec<&String> = b.keys().collect();
            keys.sort();
            for k in keys {
                let (cport, proto) = k.split_once('/').unwrap_or((k, "tcp"));
                for bind in b[k].as_array().into_iter().flatten() {
                    let hp = bind.get("HostPort").and_then(Value::as_str).unwrap_or("");
                    if hp.is_empty() {
                        continue;
                    }
                    let ip = bind.get("HostIp").and_then(Value::as_str).unwrap_or("");
                    let mut p = if ip.is_empty() || ip == "0.0.0.0" { format!("{hp}:{cport}") } else { format!("{ip}:{hp}:{cport}") };
                    if proto != "tcp" {
                        p += &format!("/{proto}");
                    }
                    ports.push(p);
                }
            }
        }
        let mut volumes = Vec::new();
        for m in c.get("Mounts").and_then(Value::as_array).into_iter().flatten() {
            let dest = m.get("Destination").and_then(Value::as_str).unwrap_or("");
            let src = match m.get("Type").and_then(Value::as_str) {
                Some("volume") => m.get("Name").and_then(Value::as_str).unwrap_or(""),
                Some("bind") => m.get("Source").and_then(Value::as_str).unwrap_or(""),
                _ => continue,
            };
            if src.is_empty() || dest.is_empty() {
                continue;
            }
            let ro = m.get("RW").and_then(Value::as_bool) == Some(false);
            volumes.push(format!("{src}:{dest}{}", if ro { ":ro" } else { "" }));
        }
        let mut env = Vec::new();
        for e in c.get("Config").and_then(|x| x.get("Env")).and_then(Value::as_array).into_iter().flatten() {
            let Some(e) = e.as_str() else { continue };
            // What every container gets, and what the image sets itself.
            if image_env.iter().any(|i| i == e) || e.starts_with("container=") || e.starts_with("HOSTNAME=") || e.starts_with("HOME=") || e.starts_with("TERM=") {
                continue;
            }
            if let Some((k, v)) = e.split_once('=') {
                env.push((k.to_string(), v.to_string()));
            }
        }
        let gpu = c
            .get("HostConfig")
            .and_then(|h| h.get("Devices"))
            .and_then(Value::as_array)
            .is_some_and(|d| d.iter().any(|x| x.to_string().contains("nvidia")));
        Ok(QuadletSpec { name, image, ports, volumes, env, gpu })
    }
}

/// `podman image inspect IMAGE`: its Config.Env.
pub fn image_env(inspect: &str) -> Vec<String> {
    let v: Value = serde_json::from_str(inspect).unwrap_or(Value::Null);
    let i = v.as_array().and_then(|a| a.first()).unwrap_or(&v);
    i.get("Config")
        .and_then(|c| c.get("Env"))
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(Value::as_str).map(String::from).collect())
        .unwrap_or_default()
}

/// The Run a container form.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct RunForm {
    pub image: String,
    pub name: String,
    /// One per line or comma: 8080:80
    pub ports: String,
    /// One per line or comma: /home/you/media:/media
    pub volumes: String,
    /// One per line: KEY=value
    pub env: String,
    pub service: bool,
    pub gpu: bool,
}

fn items(t: &str) -> Vec<String> {
    t.split(['\n', ',']).map(str::trim).filter(|x| !x.is_empty()).map(String::from).collect()
}

impl RunForm {
    pub fn spec(&self) -> QuadletSpec {
        QuadletSpec {
            name: self.name.trim().to_string(),
            image: self.image.trim().to_string(),
            ports: items(&self.ports),
            volumes: items(&self.volumes),
            env: self.env.lines().filter_map(|l| l.trim().split_once('=')).map(|(k, v)| (k.trim().to_string(), v.to_string())).collect(),
            gpu: self.gpu,
        }
    }

    pub fn problem(&self, taken: &[String]) -> Option<String> {
        if self.image.trim().is_empty() {
            return Some("Which image? e.g. docker.io/jellyfin/jellyfin:latest".into());
        }
        let n = self.name.trim();
        if !valid_name(n) {
            return Some("A name: letters, digits, dots, dashes and underscores.".into());
        }
        if taken.iter().any(|t| t == n) {
            return Some(format!("{n} is taken by another container or service."));
        }
        for p in items(&self.ports) {
            let core = p.split('/').next().unwrap_or("");
            let parts: Vec<&str> = core.split(':').collect();
            let ok = (2..=3).contains(&parts.len()) && parts[parts.len() - 2..].iter().all(|x| x.parse::<u16>().is_ok());
            if !ok {
                return Some(format!("Ports look like 8080:80 (this computer's port, then the container's): {p}"));
            }
        }
        for v in items(&self.volumes) {
            if v.split(':').count() < 2 {
                return Some(format!("Folders look like /home/you/media:/media: {v}"));
            }
        }
        if self.env.lines().any(|l| !l.trim().is_empty() && !l.contains('=')) {
            return Some("Environment lines look like KEY=value.".into());
        }
        None
    }

    /// Without "as a service": a plain `podman run`.
    pub fn run_argv(&self) -> Vec<String> {
        let s = self.spec();
        let mut a = argv(&["podman", "run", "--detach", "--name", &s.name]);
        for p in &s.ports {
            a.extend(["--publish".into(), p.clone()]);
        }
        for v in &s.volumes {
            a.extend(["--volume".into(), v.clone()]);
        }
        for (k, v) in &s.env {
            a.extend(["--env".into(), format!("{k}={v}")]);
        }
        if s.gpu {
            a.extend(["--device".into(), "nvidia.com/gpu=all".into()]);
        }
        a.push(s.image);
        a
    }
}

pub fn quadlet_dir(home: &str) -> String {
    format!("{home}/.config/containers/systemd")
}

pub fn service_action(verb: &str, unit: &str) -> Vec<String> {
    argv(&["systemctl", "--user", verb, unit])
}

pub fn service_logs(unit: &str) -> Vec<String> {
    argv(&["journalctl", "--user", "-u", unit, "-n", "300", "--no-pager", "-o", "short"])
}

#[cfg(test)]
mod tests {
    use super::*;

    const PS: &str = r#"[
      {"Id": "0a37", "Names": ["dev"], "Image": "docker.io/library/ubuntu:24.04", "State": "exited",
       "Status": "Exited (143) 2 weeks ago", "Labels": {"manager": "distrobox"}, "Ports": null},
      {"Id": "77aa", "Names": ["jellyfin"], "Image": "docker.io/jellyfin/jellyfin:latest", "State": "running",
       "Status": "Up 3 hours", "Labels": {"PODMAN_SYSTEMD_UNIT": "jellyfin.service"},
       "Ports": [{"host_ip": "", "container_port": 8096, "host_port": 8096, "range": 1, "protocol": "tcp"}]},
      {"Id": "99bb", "Names": ["Scratch"], "Image": "docker.io/library/alpine:latest", "State": "created",
       "Status": "Created", "Labels": null,
       "Ports": [{"host_ip": "127.0.0.1", "container_port": 53, "host_port": 5353, "range": 1, "protocol": "udp"}]}
    ]"#;

    #[test]
    fn ps_sorts_out_boxes_services_and_containers() {
        let c = parse_ps(PS).unwrap();
        assert_eq!(c.iter().map(|x| x.name.as_str()).collect::<Vec<_>>(), ["dev", "jellyfin", "Scratch"]);
        assert!(c[0].distrobox && c[0].unit.is_none() && !c[0].running());
        assert_eq!(c[1].unit.as_deref(), Some("jellyfin.service"));
        assert_eq!(c[1].ports, ["8096→8096"]);
        assert_eq!(c[2].ports, ["127.0.0.1:5353→53/udp"]);
        assert!(parse_ps("[]").unwrap().is_empty());
        assert!(parse_ps("nope").is_err());
    }

    #[test]
    fn images() {
        let i = parse_images(r#"[{"Id": "d9b8c34d61a9b9a3", "Names": ["docker.io/library/ubuntu:24.04"], "Size": 161397108, "Containers": 1},
                                 {"Id": "abcdef0123456789", "Names": null, "Size": 5000, "Containers": 0}]"#).unwrap();
        assert_eq!(i[1].name, "docker.io/library/ubuntu:24.04");
        assert!(i[0].name.starts_with("<none> abcdef012345"));
        assert_eq!(human(161397108), "161 MB");
        assert_eq!(human(2_400_000_000), "2.4 GB");
    }

    #[test]
    fn new_box() {
        let mut b = NewBox { name: "dev".into(), ..Default::default() };
        assert!(b.problem(&["dev".into()]).unwrap().contains("already"));
        assert_eq!(b.problem(&[]), None);
        assert_eq!(b.argv("/home/u"), ["distrobox", "create", "--yes", "--name", "dev", "--image", "docker.io/library/ubuntu:24.04"]);
        b.nvidia = true;
        b.own_home = true;
        b.distro = DISTROS.len();
        b.image = " quay.io/toolbx/arch-toolbox ".into();
        assert_eq!(b.argv("/home/u")[6..], ["quay.io/toolbx/arch-toolbox", "--nvidia", "--home", "/home/u/.local/share/boxes/dev"]);
        b.image.clear();
        assert!(b.problem(&[]).unwrap().contains("image"));
        assert!(!valid_name("-x") && !valid_name("a b") && !valid_name("") && valid_name("my_box.2"));
    }

    #[test]
    fn box_apps_are_desktop_files() {
        assert_eq!(parse_box_apps("code.desktop\nmimeinfo.cache\norg.gnome.gedit.desktop\n"), ["code", "org.gnome.gedit"]);
        assert_eq!(box_export("dev", "code"), ["distrobox", "enter", "dev", "--", "distrobox-export", "--app", "code"]);
    }

    #[test]
    fn quadlet_round_trip() {
        let spec = QuadletSpec {
            name: "web".into(),
            image: "docker.io/library/nginx:latest".into(),
            ports: vec!["8080:80".into()],
            volumes: vec!["/home/u/site:/usr/share/nginx/html:ro".into()],
            env: vec![("TZ".into(), "America/Toronto".into()), ("MOTD".into(), "hello there".into())],
            gpu: true,
        };
        let t = spec.render();
        assert!(t.starts_with(MARK));
        assert!(t.contains("ContainerName=web\n") && t.contains("PublishPort=8080:80\n"));
        assert!(t.contains("Environment=TZ=America/Toronto\n") && t.contains("Environment=\"MOTD=hello there\"\n"));
        assert!(t.contains("AddDevice=nvidia.com/gpu=all\n") && t.ends_with("WantedBy=default.target\n"));
        let back = parse_quadlet("web", "/x/web.container", &t, false);
        assert_eq!((back.image.as_str(), back.ports.clone(), back.autostart, back.ours),
                   ("docker.io/library/nginx:latest", vec!["8080:80".to_string()], true, true));
        let theirs = parse_quadlet("db", "/x/db.container", "[Container]\nImage=postgres\n", true);
        assert!(!theirs.ours && !theirs.autostart && theirs.system);
    }

    #[test]
    fn keep_running_at_boot_reads_the_container() {
        let inspect = r#"[{"Name": "web", "ImageName": "docker.io/library/nginx:latest",
          "Config": {"Image": "docker.io/library/nginx:latest",
                     "Env": ["PATH=/usr/local/bin:/usr/bin", "NGINX_VERSION=1.29", "container=podman", "HOSTNAME=abc", "TZ=UTC"]},
          "HostConfig": {"PortBindings": {"80/tcp": [{"HostIp": "", "HostPort": "8080"}],
                                          "53/udp": [{"HostIp": "127.0.0.1", "HostPort": "5353"}]},
                         "Devices": []},
          "Mounts": [{"Type": "bind", "Source": "/home/u/site", "Destination": "/usr/share/nginx/html", "RW": false},
                     {"Type": "volume", "Name": "cache", "Source": "/home/u/.local/share/containers/storage/volumes/cache/_data", "Destination": "/var/cache/nginx", "RW": true},
                     {"Type": "tmpfs", "Destination": "/run"}]}]"#;
        let img = image_env(r#"[{"Config": {"Env": ["PATH=/usr/local/bin:/usr/bin", "NGINX_VERSION=1.29"]}}]"#);
        let s = QuadletSpec::from_inspect(inspect, &img).unwrap();
        assert_eq!(s.name, "web");
        assert_eq!(s.ports, ["127.0.0.1:5353:53/udp", "8080:80"]);
        assert_eq!(s.volumes, ["/home/u/site:/usr/share/nginx/html:ro", "cache:/var/cache/nginx"]);
        assert_eq!(s.env, [("TZ".to_string(), "UTC".to_string())]);
        assert!(!s.gpu);
        assert!(QuadletSpec::from_inspect("[{}]", &[]).is_err());
    }

    #[test]
    fn run_form() {
        let mut f = RunForm { image: "docker.io/library/nginx".into(), name: "web".into(), ports: "8080:80, 127.0.0.1:8443:443".into(),
                              volumes: "/home/u/site:/usr/share/nginx/html".into(), env: "TZ=UTC\n".into(), ..Default::default() };
        assert_eq!(f.problem(&[]), None);
        assert_eq!(f.run_argv(), ["podman", "run", "--detach", "--name", "web", "--publish", "8080:80", "--publish", "127.0.0.1:8443:443",
                                  "--volume", "/home/u/site:/usr/share/nginx/html", "--env", "TZ=UTC", "docker.io/library/nginx"]);
        assert!(f.problem(&["web".into()]).is_some());
        f.ports = "eighty".into();
        assert!(f.problem(&[]).unwrap().contains("8080:80"));
        f.ports.clear();
        f.volumes = "/just/a/path".into();
        assert!(f.problem(&[]).unwrap().contains("Folders"));
        f.volumes.clear();
        f.env = "TZ".into();
        assert!(f.problem(&[]).unwrap().contains("KEY=value"));
    }
}
