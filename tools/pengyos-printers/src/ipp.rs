//! IPP/1.1 messages (RFC 8010), just enough of them to talk to CUPS.
//!
//! Std only and pure: [`Request::encode`] builds the bytes CUPS reads, and
//! [`Response::decode`] reads the bytes it answers with. The transport (HTTP
//! over CUPS' local socket) is `cups.rs`; the tests decode responses captured
//! from a real cupsd (`tests/data/*.ipp`).

use std::fmt;

/// Operation ids (RFC 8011 §5.4.15, and CUPS' own in the 0x4000 range).
pub mod op {
    pub const PRINT_JOB: u16 = 0x0002;
    pub const CANCEL_JOB: u16 = 0x0008;
    pub const GET_JOBS: u16 = 0x000A;
    pub const GET_PRINTER_ATTRIBUTES: u16 = 0x000B;
    pub const HOLD_JOB: u16 = 0x000C;
    pub const RELEASE_JOB: u16 = 0x000D;
    pub const PAUSE_PRINTER: u16 = 0x0010;
    pub const RESUME_PRINTER: u16 = 0x0011;
    pub const CANCEL_JOBS: u16 = 0x0038;
    pub const CUPS_GET_DEFAULT: u16 = 0x4001;
    pub const CUPS_GET_PRINTERS: u16 = 0x4002;
    pub const CUPS_ADD_MODIFY_PRINTER: u16 = 0x4003;
    pub const CUPS_DELETE_PRINTER: u16 = 0x4004;
    pub const CUPS_SET_DEFAULT: u16 = 0x400A;
    pub const CUPS_GET_DEVICES: u16 = 0x400B;
    pub const CUPS_GET_PPDS: u16 = 0x400C;
}

/// Delimiter tags: the attribute groups.
pub mod group {
    pub const OPERATION: u8 = 0x01;
    pub const JOB: u8 = 0x02;
    pub const END: u8 = 0x03;
    pub const PRINTER: u8 = 0x04;
    pub const UNSUPPORTED: u8 = 0x05;
}

/// Value tags.
pub mod tag {
    pub const UNSUPPORTED: u8 = 0x10;
    pub const UNKNOWN: u8 = 0x12;
    pub const NO_VALUE: u8 = 0x13;
    pub const INTEGER: u8 = 0x21;
    pub const BOOLEAN: u8 = 0x22;
    pub const ENUM: u8 = 0x23;
    pub const OCTET_STRING: u8 = 0x30;
    pub const DATE_TIME: u8 = 0x31;
    pub const RESOLUTION: u8 = 0x32;
    pub const RANGE: u8 = 0x33;
    pub const BEG_COLLECTION: u8 = 0x34;
    pub const TEXT_WITH_LANGUAGE: u8 = 0x35;
    pub const NAME_WITH_LANGUAGE: u8 = 0x36;
    pub const END_COLLECTION: u8 = 0x37;
    pub const TEXT: u8 = 0x41;
    pub const NAME: u8 = 0x42;
    pub const KEYWORD: u8 = 0x44;
    pub const URI: u8 = 0x45;
    pub const URI_SCHEME: u8 = 0x46;
    pub const CHARSET: u8 = 0x47;
    pub const LANGUAGE: u8 = 0x48;
    pub const MIME_TYPE: u8 = 0x49;
    pub const MEMBER_NAME: u8 = 0x4A;
}

#[derive(Clone, Debug, PartialEq)]
pub enum Value {
    Integer(i32),
    Boolean(bool),
    Enum(i32),
    /// text, name, keyword, uri, charset, language, mime type…: the tag says which.
    Str(u8, String),
    /// Anything else (dates, resolutions, ranges, collections, out-of-band
    /// values): kept as raw bytes so decoding never fails on an attribute the
    /// app does not read.
    Other(u8, Vec<u8>),
}

impl Value {
    pub fn text(s: &str) -> Value {
        Value::Str(tag::TEXT, s.into())
    }
    pub fn name(s: &str) -> Value {
        Value::Str(tag::NAME, s.into())
    }
    pub fn keyword(s: &str) -> Value {
        Value::Str(tag::KEYWORD, s.into())
    }
    pub fn uri(s: &str) -> Value {
        Value::Str(tag::URI, s.into())
    }
    pub fn mime(s: &str) -> Value {
        Value::Str(tag::MIME_TYPE, s.into())
    }

    fn tag(&self) -> u8 {
        match self {
            Value::Integer(_) => tag::INTEGER,
            Value::Boolean(_) => tag::BOOLEAN,
            Value::Enum(_) => tag::ENUM,
            Value::Str(t, _) | Value::Other(t, _) => *t,
        }
    }

    fn bytes(&self) -> Vec<u8> {
        match self {
            Value::Integer(i) | Value::Enum(i) => i.to_be_bytes().to_vec(),
            Value::Boolean(b) => vec![u8::from(*b)],
            Value::Str(_, s) => s.as_bytes().to_vec(),
            Value::Other(_, b) => b.clone(),
        }
    }

    fn from_wire(t: u8, v: &[u8]) -> Value {
        let int = || <[u8; 4]>::try_from(v).ok().map(i32::from_be_bytes);
        match t {
            tag::INTEGER => int().map(Value::Integer).unwrap_or(Value::Other(t, v.to_vec())),
            tag::ENUM => int().map(Value::Enum).unwrap_or(Value::Other(t, v.to_vec())),
            tag::BOOLEAN if v.len() == 1 => Value::Boolean(v[0] != 0),
            0x41..=0x49 => Value::Str(t, String::from_utf8_lossy(v).into_owned()),
            _ => Value::Other(t, v.to_vec()),
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Value::Str(_, s) => Some(s),
            _ => None,
        }
    }

    pub fn as_int(&self) -> Option<i32> {
        match self {
            Value::Integer(i) | Value::Enum(i) => Some(*i),
            _ => None,
        }
    }

    pub fn as_bool(&self) -> Option<bool> {
        match self {
            Value::Boolean(b) => Some(*b),
            _ => None,
        }
    }
}

/// One attribute and all its values (a 1setOf arrives as one name followed by
/// values with an empty name; it is folded back into one attribute here).
#[derive(Clone, Debug, PartialEq)]
pub struct Attribute {
    pub name: String,
    pub values: Vec<Value>,
}

/// One attribute group: the operation attributes, or one printer, job, device…
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Group {
    pub tag: u8,
    pub attrs: Vec<Attribute>,
}

impl Group {
    pub fn get(&self, name: &str) -> Option<&Attribute> {
        self.attrs.iter().find(|a| a.name == name)
    }
    pub fn str(&self, name: &str) -> Option<&str> {
        self.get(name).and_then(|a| a.values.first()).and_then(Value::as_str)
    }
    /// The attribute as a string, or "" (CUPS sends empty text for unset ones).
    pub fn string(&self, name: &str) -> String {
        self.str(name).unwrap_or_default().to_string()
    }
    pub fn strs(&self, name: &str) -> Vec<String> {
        self.get(name)
            .map(|a| a.values.iter().filter_map(Value::as_str).map(str::to_string).collect())
            .unwrap_or_default()
    }
    pub fn int(&self, name: &str) -> Option<i32> {
        self.get(name).and_then(|a| a.values.first()).and_then(Value::as_int)
    }
    pub fn ints(&self, name: &str) -> Vec<i32> {
        self.get(name).map(|a| a.values.iter().filter_map(Value::as_int).collect()).unwrap_or_default()
    }
    pub fn bool(&self, name: &str) -> Option<bool> {
        self.get(name).and_then(|a| a.values.first()).and_then(Value::as_bool)
    }
}

/// An IPP request: operation attributes beyond the two every request carries
/// (charset and language, added by [`Request::encode`]), an optional job or
/// printer group, and the document data that follows (Print-Job).
#[derive(Clone, Debug, PartialEq)]
pub struct Request {
    pub op: u16,
    pub id: u32,
    pub operation: Vec<(String, Vec<Value>)>,
    /// The printer attributes of CUPS-Add-Modify-Printer.
    pub printer: Vec<(String, Vec<Value>)>,
    pub data: Vec<u8>,
}

impl Request {
    pub fn new(op: u16) -> Request {
        Request { op, id: 1, operation: Vec::new(), printer: Vec::new(), data: Vec::new() }
    }

    pub fn attr(mut self, name: &str, value: Value) -> Request {
        self.operation.push((name.into(), vec![value]));
        self
    }

    pub fn attrs(mut self, name: &str, values: Vec<Value>) -> Request {
        self.operation.push((name.into(), values));
        self
    }

    pub fn printer_attr(mut self, name: &str, value: Value) -> Request {
        self.printer.push((name.into(), vec![value]));
        self
    }

    /// `requested-attributes`, the keywords the answer should carry.
    pub fn want(self, names: &[&str]) -> Request {
        self.attrs("requested-attributes", names.iter().map(|n| Value::keyword(n)).collect())
    }

    pub fn encode(&self) -> Vec<u8> {
        let mut b = Vec::with_capacity(256 + self.data.len());
        b.extend_from_slice(&[2, 0]); // IPP 2.0, what CUPS 2.x speaks
        b.extend_from_slice(&self.op.to_be_bytes());
        b.extend_from_slice(&self.id.to_be_bytes());
        b.push(group::OPERATION);
        put(&mut b, "attributes-charset", &Value::Str(tag::CHARSET, "utf-8".into()));
        put(&mut b, "attributes-natural-language", &Value::Str(tag::LANGUAGE, "en".into()));
        for (name, values) in &self.operation {
            put_all(&mut b, name, values);
        }
        if !self.printer.is_empty() {
            b.push(group::PRINTER);
            for (name, values) in &self.printer {
                put_all(&mut b, name, values);
            }
        }
        b.push(group::END);
        b.extend_from_slice(&self.data);
        b
    }
}

fn put(b: &mut Vec<u8>, name: &str, v: &Value) {
    let bytes = v.bytes();
    b.push(v.tag());
    b.extend_from_slice(&(name.len() as u16).to_be_bytes());
    b.extend_from_slice(name.as_bytes());
    b.extend_from_slice(&(bytes.len() as u16).to_be_bytes());
    b.extend_from_slice(&bytes);
}

fn put_all(b: &mut Vec<u8>, name: &str, values: &[Value]) {
    for (i, v) in values.iter().enumerate() {
        put(b, if i == 0 { name } else { "" }, v);
    }
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Response {
    pub status: u16,
    pub id: u32,
    pub groups: Vec<Group>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct DecodeError(pub String);

impl fmt::Display for DecodeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "CUPS sent an answer the app cannot read ({})", self.0)
    }
}

impl Response {
    pub fn decode(b: &[u8]) -> Result<Response, DecodeError> {
        if b.len() < 9 {
            return Err(DecodeError(format!("{} bytes", b.len())));
        }
        let status = u16::from_be_bytes([b[2], b[3]]);
        let id = u32::from_be_bytes([b[4], b[5], b[6], b[7]]);
        let mut r = Response { status, id, groups: Vec::new() };
        let mut i = 8;
        let take = |i: &mut usize, n: usize| -> Result<&[u8], DecodeError> {
            let s = b.get(*i..*i + n).ok_or_else(|| DecodeError(format!("cut short at byte {i}")))?;
            *i += n;
            Ok(s)
        };
        // Collections nest; their member values are not needed, so a depth
        // counter just skips everything between a begCollection and its end.
        let mut depth = 0usize;
        loop {
            let t = *take(&mut i, 1)?.first().unwrap_or(&group::END);
            if t < 0x10 {
                if t == group::END {
                    break;
                }
                r.groups.push(Group { tag: t, attrs: Vec::new() });
                continue;
            }
            let n = u16::from_be_bytes(take(&mut i, 2)?.try_into().unwrap_or([0, 0])) as usize;
            let name = String::from_utf8_lossy(take(&mut i, n)?).into_owned();
            let n = u16::from_be_bytes(take(&mut i, 2)?.try_into().unwrap_or([0, 0])) as usize;
            let value = take(&mut i, n)?;
            match t {
                tag::BEG_COLLECTION => {
                    depth += 1;
                    if depth > 1 {
                        continue;
                    }
                }
                tag::END_COLLECTION => {
                    depth = depth.saturating_sub(1);
                    continue;
                }
                _ if depth > 0 => continue,
                _ => {}
            }
            let Some(g) = r.groups.last_mut() else {
                return Err(DecodeError("an attribute before any group".into()));
            };
            let v = Value::from_wire(t, value);
            match g.attrs.last_mut() {
                Some(a) if name.is_empty() => a.values.push(v),
                _ => g.attrs.push(Attribute { name, values: vec![v] }),
            }
        }
        Ok(r)
    }

    /// The groups of one kind (each printer, job, device or driver is one).
    pub fn groups(&self, tag: u8) -> impl Iterator<Item = &Group> {
        self.groups.iter().filter(move |g| g.tag == tag)
    }

    /// CUPS' own explanation, when it gave one.
    pub fn message(&self) -> Option<String> {
        self.groups(group::OPERATION)
            .find_map(|g| g.str("status-message"))
            .filter(|m| !m.is_empty())
            .map(str::to_string)
    }

    /// successful-ok and its -ignored/-conflicting variants (0x0000..=0x00ff).
    pub fn ok(&self) -> bool {
        self.status < 0x0100
    }
}

/// Status codes the app explains to people (RFC 8011 §13.1, CUPS' 0x04xx).
pub mod status {
    pub const NOT_FOUND: u16 = 0x0406;
    pub const FORBIDDEN: u16 = 0x0401;
    pub const NOT_AUTHENTICATED: u16 = 0x0402;
    pub const NOT_AUTHORIZED: u16 = 0x0403;
    pub const NOT_POSSIBLE: u16 = 0x0404;
    pub const CUPS_AUTHENTICATION_CANCELED: u16 = 0x1000;
}
